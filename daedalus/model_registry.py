"""Model Registry — "profiles" for each LLM model enabling smart worker routing.

Persistent store: daedalus:model_registry (JSON list of ModelEntry)
Auto-mode:        daedalus:worker_auto_mode (bool)
Named configs:    daedalus:worker_named_configs (dict id→config)

Public API:
    get_registry()                         → list[ModelEntry]
    get_model(id)                          → ModelEntry | None
    set_model(id, fields)                  → None
    delete_model(id)                       → bool
    reset_to_defaults()                    → None
    update_stats(id, success, latency_ms)  → None   (called by workers after each call)
    select_for_task(task_desc, budget)     → ModelEntry | None
    explain_selection(task_desc, budget)   → dict
    get_auto_mode()                        → bool
    set_auto_mode(enabled)                 → None
    list_named_configs()                   → list[NamedConfig]
    save_named_config(name, slots, icon)   → str  (id)
    load_named_config(id)                  → NamedConfig | None
    delete_named_config(id)                → bool
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import TypedDict

log = logging.getLogger(__name__)

_DB_KEY_REGISTRY   = "daedalus:model_registry"
_DB_KEY_AUTO_MODE  = "daedalus:worker_auto_mode"
_DB_KEY_NAMED_CFGS = "daedalus:worker_named_configs"

CAPABILITY_TAGS: dict[str, str] = {
    "code":             "Code writing",
    "code_review":      "Code review / debugging",
    "planning":         "Planning and decomposition",
    "reasoning":        "Deep reasoning / chain-of-thought",
    "analysis":         "Analysis and synthesis",
    "review":           "Final review / approval",
    "writing":          "Text writing / documentation",
    "extraction":       "Structured data extraction",
    "classification":   "Classification / tagging",
    "translation":      "Translation",
    "summarization":    "Summarization",
    "math":             "Mathematics / calculations",
    "long_context":     "Long context (>64K tokens)",
    "chinese":          "Chinese language tasks",
    "vision":           "Image processing",
    "fast":             "Very fast response (<3s)",
    "cheap":            "Very cheap (<$0.30/MTok)",
    "tools":            "Function calling / tool use",
    "orchestration":    "Complex plan orchestration",
}

_KEYWORD_MAP: dict[str, list[str]] = {
    "code":          ["code", "function", "class", "script", "program", "implement",
                      "html", "css", "javascript", "python", "endpoint", "api",
                      "query", "sql", "pandas", "game", "write"],
    "code_review":   ["review", "debug", "bug", "fix", "error", "check code", "debug"],
    "planning":      ["plan", "decompose", "steps", "breakdown", "how to", "approach",
                      "architecture", "design"],
    "reasoning":     ["analyze", "reasoning", "math", "calculate", "logic", "why",
                      "multi-step", "wyckoff", "elliott"],
    "review":        ["review", "approve", "architect", "quality", "final", "assess"],
    "analysis":      ["analysis", "compare", "research", "trend", "evaluate", "strategy"],
    "writing":       ["write text", "documentation", "docs", "article", "explanation",
                      "readme", "report"],
    "extraction":    ["extract", "parse", "json", "table", "structure"],
    "classification":["classify", "tag", "categorize"],
    "summarization": ["summary", "summarize", "brief", "overview"],
    "long_context":  ["long", "document", "full file", "large", "128k"],
    "math":          ["formula", "calculate", "statistic", "percent"],
    "fast":          ["quick", "short", "fast", "1-2 sentences"],
    "cheap":         ["cheap", "budget", "minimal cost"],
}

_TIER_COST_WEIGHT = {
    "fast":      1.0,
    "cheap":     1.5,
    "balanced":  0.7,
    "deep":      0.5,
    "coder":     0.8,
    "reasoning": 0.3,
    "review":    0.2,
}

DEFAULT_REGISTRY: list[dict] = [
    {
        "id":             "gemini-flash-openrouter",
        "display_name":   "Gemini 2.5 Flash",
        "provider":       "openrouter",
        "model_id":       "google/gemini-2.5-flash",
        "capabilities":   ["fast", "extraction", "classification", "cheap",
                           "translation", "summarization", "vision"],
        "strengths":      "Fastest (~1-3s), vision, good JSON/classification, huge context (1M)",
        "weaknesses":     "Weaker reasoning and code than DeepSeek",
        "cost_input":     0.15,
        "cost_output":    0.60,
        "context_window": 1_000_000,
        "tier":           "fast",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "claude-haiku-openrouter",
        "success_rate":   0.97,
        "avg_latency_ms": 2000,
        "worker_slot":    "fast",
    },
    {
        "id":             "claude-haiku-openrouter",
        "display_name":   "Claude Haiku 3.5",
        "provider":       "openrouter",
        "model_id":       "anthropic/claude-3-5-haiku",
        "capabilities":   ["fast", "extraction", "classification", "cheap",
                           "translation", "summarization", "tools"],
        "strengths":      "Obedient, fast, stable at following instructions, tool calling",
        "weaknesses":     "Limited reasoning, shorter output, more expensive than Gemini Flash",
        "cost_input":     0.80,
        "cost_output":    4.0,
        "context_window": 200_000,
        "tier":           "fast",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "gemini-flash-openrouter",
        "success_rate":   0.96,
        "avg_latency_ms": 3000,
        "worker_slot":    "fast",
    },
    {
        "id":             "llama-3.3-openrouter",
        "display_name":   "Llama 3.3 70B",
        "provider":       "openrouter",
        "model_id":       "meta-llama/llama-3.3-70b-instruct",
        "capabilities":   ["balanced", "analysis", "research", "writing", "cheap", "tools"],
        "strengths":      "Good price/quality balance, analysis, tool calling",
        "weaknesses":     "Hallucinations on specific facts, weaker code than DeepSeek",
        "cost_input":     0.20,
        "cost_output":    0.60,
        "context_window": 128_000,
        "tier":           "balanced",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-novita",
        "success_rate":   0.94,
        "avg_latency_ms": 5000,
        "worker_slot":    "balanced",
    },
    {
        "id":             "deepseek-v3-novita",
        "display_name":   "DeepSeek V3 (Novita)",
        "provider":       "novita",
        "model_id":       "deepseek/deepseek-v3",
        "capabilities":   ["code", "extraction", "cheap", "writing", "analysis", "planning"],
        "strengths":      "Excellent code, Novita cheaper than OpenRouter, follows instructions precisely",
        "weaknesses":     "No tool calling, weaker at deep reasoning",
        "cost_input":     0.22,
        "cost_output":    0.88,
        "context_window": 64_000,
        "tier":           "deep",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-openrouter",
        "success_rate":   0.95,
        "avg_latency_ms": 7000,
        "worker_slot":    "deep",
    },
    {
        "id":             "deepseek-v3-openrouter",
        "display_name":   "DeepSeek V3.2 (OpenRouter)",
        "provider":       "openrouter",
        "model_id":       "deepseek/deepseek-v3.2",
        "capabilities":   ["code", "extraction", "writing", "analysis", "planning", "cheap"],
        "strengths":      "Latest V3, excellent code, complex synthesis",
        "weaknesses":     "No tool calling, slower than Novita version",
        "cost_input":     0.27,
        "cost_output":    1.10,
        "context_window": 64_000,
        "tier":           "deep",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-novita",
        "success_rate":   0.95,
        "avg_latency_ms": 8000,
        "worker_slot":    "deep",
    },
    {
        "id":             "qwen3-coder-novita",
        "display_name":   "Qwen3 Coder 30B (Novita)",
        "provider":       "novita",
        "model_id":       "qwen/qwen3-coder-30b-a3b-instruct",
        "capabilities":   ["code", "code_review", "cheap"],
        "strengths":      "Specialized coder, debugging, code review, very cheap",
        "weaknesses":     "Narrower use cases outside code, smaller context",
        "cost_input":     0.15,
        "cost_output":    0.60,
        "context_window": 32_000,
        "tier":           "coder",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-novita",
        "success_rate":   0.93,
        "avg_latency_ms": 4000,
        "worker_slot":    "coder",
    },
    {
        "id":             "kimi-k2-novita",
        "display_name":   "Kimi K2 (Novita)",
        "provider":       "novita",
        "model_id":       "moonshotai/kimi-k2-instruct",
        "capabilities":   ["code", "planning", "analysis", "long_context", "chinese", "tools"],
        "strengths":      "Agentic model, good at code+planning combos, tool calling",
        "weaknesses":     "Newer model, less battle-tested in production",
        "cost_input":     0.60,
        "cost_output":    2.50,
        "context_window": 128_000,
        "tier":           "balanced",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "llama-3.3-openrouter",
        "success_rate":   0.90,
        "avg_latency_ms": 8000,
        "worker_slot":    "balanced",
    },
    {
        "id":             "deepseek-r1-openrouter",
        "display_name":   "DeepSeek R1-0528",
        "provider":       "openrouter",
        "model_id":       "deepseek/deepseek-r1-0528",
        "capabilities":   ["reasoning", "planning", "math", "analysis"],
        "strengths":      "Chain-of-thought reasoning, planning, mathematics, multi-step analysis",
        "weaknesses":     "Slow (30-100s), verbose, not for simple tasks",
        "cost_input":     0.55,
        "cost_output":    2.19,
        "context_window": 64_000,
        "tier":           "reasoning",
        "supports_tools": False,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-openrouter",
        "success_rate":   0.90,
        "avg_latency_ms": 45000,
        "worker_slot":    "planner",
    },
    {
        "id":             "claude-sonnet-openrouter",
        "display_name":   "Claude Sonnet 4.5",
        "provider":       "openrouter",
        "model_id":       "anthropic/claude-sonnet-4-5",
        "capabilities":   ["review", "reasoning", "orchestration", "writing", "analysis", "tools"],
        "strengths":      "Nuanced understanding, final review, complex reasoning, tool calling",
        "weaknesses":     "Expensive ($3/MTok in, $15/MTok out) — use only when needed",
        "cost_input":     3.0,
        "cost_output":    15.0,
        "context_window": 200_000,
        "tier":           "review",
        "supports_tools": True,
        "enabled":        True,
        "fallback_to":    "deepseek-v3-openrouter",
        "success_rate":   0.98,
        "avg_latency_ms": 8000,
        "worker_slot":    "reviewer",
    },
]


def _db_read(key: str, default):
    try:
        from .storage import kv_get
        raw = kv_get(key)
        if raw is None:
            return default
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return default


def _db_write(key: str, value) -> None:
    try:
        from .storage import kv_set
        kv_set(key, json.dumps(value))
    except Exception as exc:
        log.warning("model_registry: store write failed [%s]: %s", key, exc)


def get_registry() -> list[dict]:
    """Return full model registry (seed + any store overrides/additions)."""
    stored = _db_read(_DB_KEY_REGISTRY, None)
    if stored is None:
        return [dict(e) for e in DEFAULT_REGISTRY]
    return stored


def get_model(model_id: str) -> dict | None:
    for entry in get_registry():
        if entry.get("id") == model_id:
            return entry
    return None


def set_model(model_id: str, fields: dict) -> None:
    """Upsert a model entry. `fields` merged into existing or created fresh."""
    registry = get_registry()
    for i, entry in enumerate(registry):
        if entry.get("id") == model_id:
            registry[i] = {**entry, **fields, "id": model_id}
            _db_write(_DB_KEY_REGISTRY, registry)
            return
    registry.append({**fields, "id": model_id})
    _db_write(_DB_KEY_REGISTRY, registry)


def delete_model(model_id: str) -> bool:
    registry = get_registry()
    new = [e for e in registry if e.get("id") != model_id]
    if len(new) == len(registry):
        return False
    _db_write(_DB_KEY_REGISTRY, new)
    return True


def reset_to_defaults() -> None:
    _db_write(_DB_KEY_REGISTRY, [dict(e) for e in DEFAULT_REGISTRY])


def update_stats(model_id: str, *, success: bool, latency_ms: int) -> None:
    """EMA update of success_rate and avg_latency after each worker call."""
    entry = get_model(model_id)
    if not entry:
        return
    alpha = 0.1
    entry["success_rate"]   = round(entry.get("success_rate", 0.95) * (1 - alpha) + (1.0 if success else 0.0) * alpha, 4)
    entry["avg_latency_ms"] = int(entry.get("avg_latency_ms", 5000) * (1 - alpha) + latency_ms * alpha)
    entry["last_used"]      = int(time.time())
    set_model(model_id, entry)


def get_auto_mode() -> bool:
    return bool(_db_read(_DB_KEY_AUTO_MODE, False))


def set_auto_mode(enabled: bool) -> None:
    _db_write(_DB_KEY_AUTO_MODE, enabled)


def _extract_caps_from_task(task_desc: str) -> list[str]:
    text = task_desc.lower()
    found: set[str] = set()
    for cap, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw in text:
                found.add(cap)
                break
    return list(found)


def _score_model(entry: dict, required_caps: list[str], budget_hint: str) -> float:
    if not entry.get("enabled", True):
        return -1.0
    caps  = entry.get("capabilities", [])
    score = 0.0
    matched = sum(1 for c in required_caps if c in caps)
    score += matched * 12.0
    score += entry.get("success_rate", 0.90) * 5.0
    cost = entry.get("cost_input", 1.0)
    if budget_hint == "cheap":
        score += max(0, (2.0 - cost) * 3.0)
    elif budget_hint == "quality":
        tier_bonus = {"review": 3, "reasoning": 2, "deep": 1, "coder": 1}.get(entry.get("tier", ""), 0)
        score += tier_bonus
    score -= entry.get("avg_latency_ms", 5000) / 20000.0
    return score


def select_for_task(
    task_desc: str,
    budget_hint: str = "balanced",
    required_caps: list[str] | None = None,
    preferred_tier: str | None = None,
) -> dict | None:
    """Pick the best model for a task description."""
    caps = required_caps if required_caps is not None else _extract_caps_from_task(task_desc)
    registry = get_registry()
    candidates = [e for e in registry if e.get("enabled", True)]
    if preferred_tier:
        tier_cands = [e for e in candidates if e.get("tier") == preferred_tier]
        if tier_cands:
            candidates = tier_cands
    if not candidates:
        return None
    scored = [(e, _score_model(e, caps, budget_hint)) for e in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]


def explain_selection(task_desc: str, budget_hint: str = "balanced") -> dict:
    """Return selection result + reasoning for debugging/admin UI."""
    caps = _extract_caps_from_task(task_desc)
    registry = get_registry()
    candidates = [e for e in registry if e.get("enabled", True)]
    scored = sorted(
        [(e, _score_model(e, caps, budget_hint)) for e in candidates],
        key=lambda x: x[1], reverse=True,
    )
    top3 = [{"id": e.get("id"), "display_name": e.get("display_name"),
              "score": round(s, 2), "tier": e.get("tier")} for e, s in scored[:3]]
    winner = scored[0][0] if scored else None
    return {"extracted_caps": caps, "budget_hint": budget_hint, "winner": winner, "top3": top3}


def list_named_configs() -> list[dict]:
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    return list(store.values())


def save_named_config(name: str, slots: dict, icon: str = "\u2699\ufe0f") -> str:
    """Save current worker slot config as a named configuration. Returns id."""
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    cfg_id = re.sub(r"[^a-z0-9_-]", "-", name.lower())[:32]
    store[cfg_id] = {
        "id":       cfg_id,
        "name":     name,
        "icon":     icon,
        "slots":    slots,
        "saved_at": int(time.time()),
    }
    _db_write(_DB_KEY_NAMED_CFGS, store)
    return cfg_id


def load_named_config(cfg_id: str) -> dict | None:
    return _db_read(_DB_KEY_NAMED_CFGS, {}).get(cfg_id)


def delete_named_config(cfg_id: str) -> bool:
    store = _db_read(_DB_KEY_NAMED_CFGS, {})
    if cfg_id not in store:
        return False
    del store[cfg_id]
    _db_write(_DB_KEY_NAMED_CFGS, store)
    return True
