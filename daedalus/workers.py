"""Worker agents — stateless, parallel LLM calls delegated by master Daedalus.

Usage (from agent tool loop):
    from daedalus.workers import parse_worker_markers, call_workers_parallel
    calls = parse_worker_markers(raw)
    if calls:
        results = await call_workers_parallel(calls)

NEED_WORKER format emitted by master:
    <NEED_WORKER: slot | task description (can be multi-line)>

slot is one of: fast / balanced / deep / analysis / planner / coder / reviewer / auto

  fast     — quick extraction, classification, summarization (~1-3s)
  balanced — general analysis, research, writing, medium complexity (~5-15s)
  deep     — code writing, complex synthesis, strategic documents (~10-30s)
  analysis — chain-of-thought reasoning, multi-step analysis (slow, 60-100s)
  planner  — task decomposition and plan creation ONLY (no execution)
  coder    — write/fix production-ready code
  reviewer — APPROVE / NEEDS_FIX / BLOCKED verdict with structured output
  auto     — registry-driven model selection based on task content

Each slot uses a distinct WORKER_PERSONA. Personas are quality frames, not
domain locks — the model adapts its role to the concrete task at hand.
Literal model-id slots (e.g. "deepseek/...") fall back to the balanced persona.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

log = logging.getLogger(__name__)

_STORE_KEY = "daedalus:worker_config"

_COMMON_RULES = (
    "COMMON RULES (apply to all slots):\n"
    "• Never emit NEED_* markers — you have no tools.\n"
    "• Never ask clarifying questions — work with the given context.\n"
    "• Never start with 'Here is the result:', 'Sure!', 'Done!' or similar fillers.\n"
    "• Reply in the language of the task (EN if task is EN, BG if task is BG).\n"
)

WORKER_PERSONAS: dict[str, str] = {

    "fast": (
        "You are a FAST EXECUTOR in the Daedalus multi-agent system.\n"
        "Called by the master agent for short subtasks with compact output.\n"
        "\n"
        "ADAPTIVE ROLE — choose based on the task:\n"
        "• Classification / tagging → list or JSON with categories\n"
        "• Short summary → 3-5 bullet points with key insights\n"
        "• Data extraction → structured table or JSON\n"
        "• Quick question → 1-3 sentences on point\n"
        "• Translation / reformatting → direct result, nothing else\n"
        "• Variant generation (names, ideas) → numbered list\n"
        "\n"
        "PRINCIPLES:\n"
        "• Result only — no intros, explanations, or conclusions unless asked.\n"
        "• Compact > verbose. Aim for max 5-10 lines.\n"
        "• Machine-readable format when applicable (JSON, Markdown table).\n"
        "• If something is unclear — assume reasonably and continue.\n"
        "\n"
        + _COMMON_RULES
    ),

    "balanced": (
        "You are a UNIVERSAL ANALYST in the Daedalus multi-agent system.\n"
        "Called for tasks requiring research, comparison, analysis, or medium-complexity synthesis.\n"
        "\n"
        "ADAPTIVE ROLE — adopt the right persona for the task:\n"
        "• Financial / market question → financial analyst (numbers, trends, risks)\n"
        "• Technical / architectural → senior engineer (trade-offs, feasibility)\n"
        "• Research / fact-check → researcher (sources, context, nuance)\n"
        "• Comparing options → product analyst (table + recommendation)\n"
        "• Business / strategy → business consultant (impact, ROI, risks)\n"
        "• Writing / communication → writer (tone, structure, audience)\n"
        "• Task planning → project planner (steps, dependencies, time)\n"
        "• Concept explanation → teacher (simple to complex, examples)\n"
        "\n"
        "PRINCIPLES:\n"
        "• Start directly with the result/conclusion — no introduction.\n"
        "• Markdown structure: ### sections, tables for comparisons, bold for key terms.\n"
        "• Every claim backed by logic — not bare opinions.\n"
        "• Specifics > generalities ('improves by 30%' > 'significantly improves').\n"
        "• Neutral tone — avoid 'amazing', 'perfect', marketing language.\n"
        "• End with 'Conclusion:' or 'Recommendation:' (1-3 sentences) when applicable.\n"
        "\n"
        + _COMMON_RULES
    ),

    "deep": (
        "You are the CHIEF SYNTHESIZER AND CRITICAL THINKER in the Daedalus system.\n"
        "Called for the most complex, critical, or final subtasks.\n"
        "Quality of your output matters more than brevity.\n"
        "\n"
        "ADAPTIVE ROLE:\n"
        "• CODE writing → senior engineer: production-quality code with error handling,\n"
        "  docstrings, type hints; 2-3 sentences on KEY design decisions.\n"
        "• CODE REVIEW → architect: point out issues with specific references (file/line/func),\n"
        "  structure as: \U0001f534 Critical | \U0001f7e1 Important | \U0001f7e2 Recommendation.\n"
        "• FINAL SYNTHESIS → integrate all data into a coherent narrative:\n"
        "  Conclusion \u2192 Rationale \u2192 Next Steps.\n"
        "• COMPLEX REASONING → reason step by step, mark assumptions,\n"
        "  note where evidence is weak.\n"
        "• STRATEGIC DOCUMENT → consultant: context \u2192 options \u2192 recommendation with rationale.\n"
        "\n"
        "PRINCIPLES:\n"
        "• Don't stop at 'good enough' — finish the thought completely.\n"
        "• Point out edge cases, risks, alternatives when relevant.\n"
        "• If something is wrong or contradictory — say so directly.\n"
        "• When you have an opinion — state it with rationale, don't sit on the fence.\n"
        "\n"
        + _COMMON_RULES
    ),

    "analysis": (
        "You are a DEEP REASONING ANALYST.\n"
        "Invoked for tasks requiring genuine multi-step chain-of-thought reasoning.\n"
        "Think step by step before answering. Show your reasoning process clearly.\n"
        "\n"
        "BEST USE CASES:\n"
        "• Technical pattern recognition (Wyckoff, Elliott, SMC, etc.)\n"
        "• Position sizing and risk/reward mathematics with all intermediate steps\n"
        "• Multi-factor scoring and ranking with explicit calculations\n"
        "• Logical debugging of quantitative strategies (find ALL bugs + root causes)\n"
        "\n"
        "OUTPUT FORMAT:\n"
        "• Show reasoning steps clearly — numbered or structured\n"
        "• State assumptions explicitly\n"
        "• Highlight conclusions with ### headers\n"
        "• For math: show every calculation step, not just the final answer\n"
        "• End with a crisp Summary/Conclusion section\n"
        "\n"
        "NOTE: Large token budget (5000 tokens) and higher latency (60-100s).\n"
        "Do NOT use for: simple questions, code generation, JSON extraction.\n"
        "\n"
        + _COMMON_RULES
    ),

    "planner": (
        "You are a STRATEGIC PLANNER in the Daedalus system.\n"
        "Your ONLY task is to create a structured plan — do NOT execute it.\n"
        "\n"
        "MANDATORY OUTPUT STRUCTURE:\n"
        "## PLAN\n"
        "**Goal:** (1 sentence)\n"
        "**Approach:** (2-3 sentences — why this strategy)\n"
        "\n"
        "**Steps:**\n"
        "1. [Step] — [file/component] — [expected output]\n"
        "2. ...\n"
        "\n"
        "**Dependencies:** (which steps block which)\n"
        "**Risks:** (top 2-3 things that could go wrong)\n"
        "**Done criterion:** (how we know it is complete)\n"
        "\n"
        "PRINCIPLES:\n"
        "• Decompose to atomic steps, executable by a single worker without extra context.\n"
        "• Name specific files, functions, tables — not abstract descriptions.\n"
        "• On ambiguity — assume reasonably and continue (no questions).\n"
        "• Plan must be sufficient for a CODER worker to execute directly.\n"
        "\n"
        + _COMMON_RULES
    ),

    "coder": (
        "You are a CODER EXECUTOR in the Daedalus system.\n"
        "You receive a concrete task (or plan) and write production-ready code.\n"
        "\n"
        "ADAPTIVE ROLE:\n"
        "• New feature → clean, typed code with error handling\n"
        "• Bug fix → identify root cause, fix minimally and precisely\n"
        "• Refactor → improve without breaking the API\n"
        "• HTML/JS/CSS → working, responsive, no external deps unless required\n"
        "\n"
        "STANDARDS:\n"
        "• Code + short comments for non-trivial decisions only — no essay explanations.\n"
        "• Typing: Python type hints, TS interfaces — mandatory.\n"
        "• Format clearly. Multiple files → mark with ```lang filename```.\n"
        "• Do not truncate — give the FULL function/file.\n"
        "• If the task is impossible or contradictory — briefly say why, suggest alternative.\n"
        "\n"
        + _COMMON_RULES
    ),

    "reviewer": (
        "You are an ARCHITECT REVIEWER in the Daedalus system.\n"
        "Your task: review the provided code/result and give an authoritative verdict.\n"
        "\n"
        "OUTPUT STRUCTURE:\n"
        "## VERDICT: APPROVED | NEEDS_FIX | BLOCKED\n"
        "(APPROVED=ready to deploy; NEEDS_FIX=specific fixes needed; BLOCKED=fundamental problem)\n"
        "\n"
        "\U0001f534 **Critical** (blocks deploy):\n"
        "- [specific problem + file/line + how to fix]\n"
        "\n"
        "\U0001f7e1 **Important** (fix before merge):\n"
        "- ...\n"
        "\n"
        "\U0001f7e2 **Recommendation** (nice-to-have):\n"
        "- ...\n"
        "\n"
        "**Summary:** [1-2 sentences final assessment]\n"
        "\n"
        "PRINCIPLES:\n"
        "• Be specific — 'line 47: missing NULL check' > 'add error handling'.\n"
        "• With NEEDS_FIX: always specify EXACTLY what must change.\n"
        "• Don't praise without reason. If it is good — say APPROVED and finish.\n"
        "• Look for: security (injection, path traversal, exposed secrets), correctness,\n"
        "  performance bottlenecks, API breaking changes, missing error paths.\n"
        "\n"
        + _COMMON_RULES
    ),
}

_DEFAULT_WORKER_PERSONA = WORKER_PERSONAS["balanced"]

WORKER_SLOTS: dict[str, dict] = {
    "fast":     {"label": "\u26a1 Fast executor (Gemini 2.5 Flash)",          "default_model": "google/gemini-2.5-flash",              "default_provider": "openrouter"},
    "balanced": {"label": "\U0001f4ca Universal analyst (Llama 3.3 70B)",     "default_model": "meta-llama/llama-3.3-70b-instruct",    "default_provider": "openrouter"},
    "deep":     {"label": "\U0001f9e0 Chief synthesizer (DeepSeek V3.2)",     "default_model": "deepseek/deepseek-v3.2",               "default_provider": "openrouter"},
    "analysis": {"label": "\U0001f52c Reasoning analyst (DeepSeek R1-0528)",  "default_model": "deepseek/deepseek-r1-0528",            "default_provider": "openrouter"},
    "planner":  {"label": "\U0001f5fa Strategic planner (DeepSeek R1)",       "default_model": "deepseek/deepseek-r1-0528",            "default_provider": "openrouter"},
    "coder":    {"label": "\U0001f4bb Coder (Qwen3 Coder / DeepSeek V3)",     "default_model": "qwen/qwen3-coder-30b-a3b-instruct",    "default_provider": "novita"},
    "reviewer": {"label": "\U0001f50d Reviewer (Claude Sonnet)",               "default_model": "anthropic/claude-sonnet-4-5",          "default_provider": "openrouter"},
    "auto":     {"label": "\U0001f916 Auto — registry-driven selection",      "default_model": "_auto_",                              "default_provider": "_auto_"},
}

_WORKER_RE = re.compile(
    r"<NEED_WORKER:\s*([a-zA-Z0-9_.\-]{1,60})\s*\|\s*(.+?)\s*>",
    re.DOTALL,
)

MAX_PARALLEL_WORKERS = 5


def parse_worker_markers(raw: str) -> list[dict]:
    """Extract all <NEED_WORKER: slot | task> from raw text. Returns [{slot, task}]."""
    if not raw or "NEED_WORKER" not in raw:
        return []
    matches = _WORKER_RE.findall(raw)
    return [{"slot": s.strip(), "task": t.strip()} for s, t in matches][:MAX_PARALLEL_WORKERS]


def _read_store() -> dict:
    try:
        from .storage import kv_get
        raw = kv_get(_STORE_KEY)
        if raw:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        pass
    return {}


def _write_store(cfg: dict) -> None:
    try:
        from .storage import kv_set
        kv_set(_STORE_KEY, json.dumps(cfg))
    except Exception as exc:
        log.warning("workers: cannot write store: %s", exc)


def get_worker_config() -> dict:
    """Return resolved worker slot configs: {slot: {model, provider, api_key, ...}}"""
    from .llm_config import PROVIDER_DEFAULTS, get_config

    master = get_config()
    store_cfg = _read_store()

    result: dict[str, dict] = {}
    for slot, meta in WORKER_SLOTS.items():
        saved = store_cfg.get(slot, {})
        if saved.get("model") and saved.get("provider"):
            provider = saved["provider"]
            pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
            api_key  = saved.get("api_key") or master["api_key"]
            base_url = saved.get("base_url") or pd["base_url"]
            result[slot] = {
                "model":    saved["model"],
                "provider": provider,
                "api_key":  api_key,
                "base_url": base_url,
                "sdk":      pd["sdk"],
                "enabled":  saved.get("enabled", True),
                "source":   "store",
            }
        else:
            default_model    = meta["default_model"]
            default_provider = meta["default_provider"]
            pd = PROVIDER_DEFAULTS.get(default_provider, PROVIDER_DEFAULTS["anthropic"])
            result[slot] = {
                "model":    default_model,
                "provider": default_provider,
                "api_key":  master["api_key"],
                "base_url": master.get("base_url") or pd["base_url"],
                "sdk":      pd["sdk"],
                "enabled":  True,
                "source":   "default",
            }
    return result


def _resolve_slot_cfg(slot: str) -> dict:
    """Resolve config for a single slot."""
    wc = get_worker_config()
    return wc.get(slot, wc.get("balanced", {}))


def set_worker_slot(slot: str, *, model: str, provider: str,
                    api_key: str = "", base_url: str = "",
                    enabled: bool = True) -> None:
    """Persist a single worker slot config to the store."""
    from .llm_config import PROVIDER_DEFAULTS
    cfg = _read_store()
    pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
    cfg[slot] = {
        "model":    model,
        "provider": provider,
        "api_key":  api_key,
        "base_url": base_url or pd["base_url"],
        "enabled":  enabled,
    }
    _write_store(cfg)


def clear_worker_config() -> None:
    """Remove all worker store overrides — revert to defaults."""
    try:
        from .storage import kv_del
        kv_del(_STORE_KEY)
    except Exception:
        pass


_SLOT_MAX_TOKENS: dict[str, int] = {
    "fast":     2048,
    "balanced": 4096,
    "deep":     6144,
    "analysis": 5000,
    "planner":  5000,
    "coder":    8192,
    "reviewer": 4096,
    "auto":     4096,
}


async def _call_worker_single(slot: str, task: str, slot_cfg: dict) -> dict:
    """Execute one worker call. Returns {slot, model, task_preview, result, ok}."""
    model      = slot_cfg.get("model", "deepseek/deepseek-v3.2")
    sdk        = slot_cfg.get("sdk", "openai")
    api_key    = slot_cfg.get("api_key", "")
    base_url   = slot_cfg.get("base_url", "")
    max_tokens = slot_cfg.get("max_tokens") or _SLOT_MAX_TOKENS.get(slot, 4096)
    persona    = WORKER_PERSONAS.get(slot, _DEFAULT_WORKER_PERSONA)

    try:
        if sdk == "anthropic":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key, base_url=base_url or None, max_retries=1)
            response = await client.messages.create(
                model=model, max_tokens=max_tokens,
                system=persona,
                messages=[{"role": "user", "content": task}],
            )
            result_text = response.content[0].text if response.content else ""
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url or None, max_retries=1)
            response = await client.chat.completions.create(
                model=model, max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": persona},
                    {"role": "user",   "content": task},
                ],
            )
            result_text = response.choices[0].message.content or ""

        return {"slot": slot, "model": model, "task_preview": task[:100],
                "result": result_text, "ok": True}
    except Exception as exc:
        log.error("Worker %s error: %s", slot, exc)
        return {"slot": slot, "model": model, "task_preview": task[:100],
                "result": f"Worker error: {exc}", "ok": False}


async def call_workers_parallel(calls: list[dict]) -> list[dict]:
    """Execute multiple worker calls in parallel. Returns list of results."""
    if not calls:
        return []
    wc = get_worker_config()
    tasks = []
    for call in calls[:MAX_PARALLEL_WORKERS]:
        slot     = call["slot"]
        task     = call["task"]
        slot_cfg = wc.get(slot, wc.get("balanced", {}))
        if not slot_cfg.get("enabled", True):
            async def _disabled(s: str = slot) -> dict:
                return {"slot": s, "ok": False, "result": f"Slot '{s}' is disabled.",
                        "model": "disabled", "task_preview": ""}
            tasks.append(_disabled())
        else:
            tasks.append(_call_worker_single(slot, task, slot_cfg))
    return list(await asyncio.gather(*tasks, return_exceptions=False))


def format_worker_results(results: list[dict]) -> str:
    """Format worker results for injection into master context."""
    lines = ["\n=== WORKER RESULTS ==="]
    for r in results:
        slot  = r.get("slot", "?")
        model = r.get("model", "?")
        ok    = "\u2713" if r.get("ok") else "\u2717"
        lines.append(f"\n[{ok} {slot.upper()} / {model}]")
        lines.append(r.get("result", ""))
    lines.append("=== END WORKER RESULTS ===\n")
    return "\n".join(lines)
