"""Daedalus Workers — parallel stateless LLM worker slots.

Workers are stateless LLM calls with specialized personas.
Multiple workers can run in parallel via asyncio.gather().

Slots:
  fast      — cheap/fast model (good for drafts, summaries)
  balanced  — general purpose (default)
  deep      — powerful model (complex analysis, architecture)
  coder     — code-specialized (implementations, fixes)
  reviewer  — review persona (approve/block proposals)

Config via WorkerConfig or config.yml:
  workers:
    fast:
      model: "google/gemini-2.5-flash"
      provider: openrouter
      api_key: "${OPENROUTER_API_KEY}"
    coder:
      model: "deepseek/deepseek-v3.2"
      provider: openrouter
      api_key: "${OPENROUTER_API_KEY}"
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_SLOT_PERSONAS: dict[str, str] = {
    "fast": (
        "You are a fast, concise assistant. Be brief and direct. "
        "Answer in plain text unless code is explicitly needed."
    ),
    "balanced": (
        "You are a balanced AI assistant. Provide clear, accurate, and complete answers. "
        "Use code blocks when writing code."
    ),
    "deep": (
        "You are a deep analytical AI. Think step-by-step, consider edge cases, "
        "and provide thorough, well-reasoned responses."
    ),
    "coder": (
        "You are a senior Python engineer. When asked to write or fix code:\n"
        "- Return ONLY the complete, working Python source code\n"
        "- No explanations, no markdown fences unless the entire response is code\n"
        "- Handle edge cases, add type hints, keep it clean\n"
        "- If something is impossible, add a comment: # UNFIXABLE: <reason>"
    ),
    "reviewer": (
        "You are a strict code reviewer. Analyze the given code or proposal and respond with:\n"
        "APPROVED: <one-line reason>\n"
        "or\n"
        "BLOCKED: <specific issues>\n\n"
        "Be concise. Focus on correctness, security, and maintainability."
    ),
}

_DEFAULT_PERSONA = _SLOT_PERSONAS["balanced"]

_SLOT_MAX_TOKENS: dict[str, int] = {
    "fast": 2048,
    "balanced": 4096,
    "deep": 6144,
    "coder": 8192,
    "reviewer": 4096,
}


@dataclass
class WorkerSlotConfig:
    model: str = "deepseek/deepseek-v3.2"
    provider: str = "openrouter"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "WorkerSlotConfig":
        return cls(
            model=d.get("model", "deepseek/deepseek-v3.2"),
            provider=d.get("provider", "openrouter"),
            api_key=d.get("api_key", "") or os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=d.get("base_url", ""),
            max_tokens=d.get("max_tokens", 4096),
            enabled=d.get("enabled", True),
        )


@dataclass
class WorkerConfig:
    """Configuration for all worker slots."""
    slots: dict[str, WorkerSlotConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkerConfig":
        cfg = cls()
        for slot_name, slot_d in d.items():
            if isinstance(slot_d, dict):
                cfg.slots[slot_name] = WorkerSlotConfig.from_dict(slot_d)
        return cfg

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        """Build config from environment variables.

        Expects:
          OPENROUTER_API_KEY   — for openrouter provider
          ANTHROPIC_API_KEY    — for anthropic provider
          NOVITA_API_KEY       — for novita provider
          DAEDALUS_FAST_MODEL  — override fast slot model
          DAEDALUS_CODER_MODEL — override coder slot model
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        defaults: dict[str, dict] = {
            "fast":     {"model": os.environ.get("DAEDALUS_FAST_MODEL", "google/gemini-2.5-flash"),     "api_key": api_key},
            "balanced": {"model": "meta-llama/llama-3.3-70b-instruct",  "api_key": api_key},
            "deep":     {"model": "deepseek/deepseek-v3.2",             "api_key": api_key},
            "coder":    {"model": os.environ.get("DAEDALUS_CODER_MODEL","deepseek/deepseek-v3.2"),      "api_key": api_key},
            "reviewer": {"model": "anthropic/claude-sonnet-4-5",        "api_key": api_key},
        }
        return cls.from_dict(defaults)

    def get(self, slot: str) -> WorkerSlotConfig | None:
        return self.slots.get(slot)

    def resolve(self, slot: str) -> WorkerSlotConfig:
        """Resolve slot → config; fall back to balanced, then built-in defaults."""
        return self.slots.get(slot) or self.slots.get("balanced") or WorkerSlotConfig()


_PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "anthropic":  "https://api.anthropic.com",
    "openai":     "https://api.openai.com/v1",
    "novita":     "https://api.novita.ai/v3/openai",
    "groq":       "https://api.groq.com/openai/v1",
    "ollama":     "http://localhost:11434/v1",
}


async def _call_single(slot: str, task: str, cfg: WorkerSlotConfig) -> dict[str, Any]:
    """Execute one worker call. Returns {slot, model, task_preview, result, ok}."""
    t0 = asyncio.get_event_loop().time()
    model = cfg.model
    provider = cfg.provider
    api_key = cfg.api_key or os.environ.get("OPENROUTER_API_KEY", "")
    base_url = cfg.base_url or _PROVIDER_BASE_URLS.get(provider, _PROVIDER_BASE_URLS["openrouter"])
    max_tokens = cfg.max_tokens or _SLOT_MAX_TOKENS.get(slot, 4096)
    persona = _SLOT_PERSONAS.get(slot, _DEFAULT_PERSONA)

    try:
        if provider == "anthropic" and not cfg.base_url:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key, max_retries=1)
            response = await client.messages.create(
                model=model, max_tokens=max_tokens,
                system=persona,
                messages=[{"role": "user", "content": task}],
            )
            result_text = response.content[0].text if response.content else ""
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            _is_reasoning = any(x in model.lower() for x in ("r1", "o3", "o4", "qwq"))
            kwargs: dict = {
                "model": model,
                "messages": [
                    {"role": "system", "content": persona},
                    {"role": "user", "content": task},
                ],
            }
            if _is_reasoning:
                kwargs["max_completion_tokens"] = max(max_tokens * 2, 2000)
            else:
                kwargs["max_tokens"] = max_tokens
            response = await client.chat.completions.create(**kwargs)
            result_text = response.choices[0].message.content or ""

        elapsed_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        log.info("Worker [%s/%s] done (%d chars, %dms)", slot, model, len(result_text), elapsed_ms)
        return {"slot": slot, "model": model, "task_preview": task[:100],
                "result": result_text, "ok": True, "elapsed_ms": elapsed_ms}

    except Exception as exc:
        elapsed_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        log.warning("Worker [%s/%s] failed: %s", slot, model, exc)
        return {"slot": slot, "model": model, "task_preview": task[:100],
                "result": f"ERROR: {exc}", "ok": False, "elapsed_ms": elapsed_ms}


async def call_workers_parallel(
    calls: list[dict[str, str]],
    config: WorkerConfig | None = None,
) -> list[dict[str, Any]]:
    """Execute multiple worker calls in parallel via asyncio.gather().

    Args:
        calls:  List of {"slot": "coder", "task": "..."} dicts.
        config: WorkerConfig instance. If None, builds from env.

    Returns:
        List of {slot, model, task_preview, result, ok, elapsed_ms} dicts
        in the same order as calls.

    Example:
        results = await call_workers_parallel([
            {"slot": "coder",    "task": "Write a binary search function"},
            {"slot": "reviewer", "task": "Review: def foo(): pass"},
        ])
    """
    if not calls:
        return []
    cfg = config or WorkerConfig.from_env()
    coros = []
    for c in calls:
        slot = c.get("slot", "balanced")
        task = c.get("task", "")
        slot_cfg = cfg.resolve(slot)
        if not slot_cfg.enabled:
            coros.append(_disabled(slot))
        else:
            coros.append(_call_single(slot, task, slot_cfg))
    return list(await asyncio.gather(*coros, return_exceptions=False))


async def _disabled(slot: str) -> dict:
    return {"slot": slot, "model": "disabled", "task_preview": "—",
            "result": f"Worker slot '{slot}' is disabled.", "ok": False}


def format_results(results: list[dict]) -> str:
    """Render worker results as a readable string."""
    lines = []
    for r in results:
        status = "✓" if r.get("ok") else "✗"
        ms = r.get("elapsed_ms", 0)
        preview = r.get("task_preview", "")[:60]
        lines.append(f"[{status}] {r['slot']}/{r['model']} ({ms}ms) — {preview}")
        if r.get("ok"):
            lines.append(f"    {r['result'][:200]}")
        else:
            lines.append(f"    ERROR: {r['result'][:200]}")
    return "\n".join(lines)
