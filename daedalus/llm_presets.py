"""
LLM Configuration Presets — predefined master + worker combinations.

Each preset applies BOTH the master config AND all worker slot configs
(fast/balanced/deep) in one call. The preset library covers common use cases
from maximum economy to maximum quality.

Use cases:
  eco       — max savings, all open-source via Novita
  balanced  — default, smart routing of cost vs quality
  premium   — max quality, Claude/GPT across the board
  code      — specialist for coding/agent tasks (Kimi K2 + Qwen Coder)
  hybrid    — cheap loop (70%) + premium finalizer via deep worker (30%)
  speed     — lowest latency, flash variants everywhere
  or_*      — OpenRouter-only variants (single key, 300+ models)
"""
from __future__ import annotations

from typing import Optional


CONFIG_PRESETS: list[dict] = [
    {
        "id": "balanced",
        "label": "\u2696\ufe0f Balanced — Default",
        "is_default": True,
        "description": (
            "Recommended configuration. DeepSeek V3.2 as master ($0.30/$0.90) "
            "balances quality and cost. Workers: fast wrap-up (Llama 8B), "
            "responsive tool loop (Llama 3.3 70B), powerful Qwen 235B for deep tasks."
        ),
        "budget": "~$0.30-1.50 / 1M tokens",
        "master":   {"provider": "novita", "model": "deepseek/deepseek-v3.2"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "meta-llama/llama-3.3-70b-instruct"},
        "deep":     {"provider": "novita", "model": "qwen/qwen3-235b-a22b-instruct-2507"},
    },
    {
        "id": "eco",
        "label": "\U0001f331 Eco — Maximum savings",
        "is_default": False,
        "description": (
            "For massive batch tasks and discovery loops. All open-source via "
            "Novita, no premium models. Sacrifices some quality for dramatically "
            "lower cost."
        ),
        "budget": "~$0.02-0.90 / 1M tokens",
        "master":   {"provider": "novita", "model": "meta-llama/llama-3.3-70b-instruct"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "inclusionai/ling-2.6-flash"},
        "deep":     {"provider": "novita", "model": "deepseek/deepseek-v3.2"},
    },
    {
        "id": "premium",
        "label": "\U0001f680 Premium — Maximum quality",
        "is_default": False,
        "description": (
            "For critical tasks where quality matters more than cost. "
            "Claude Sonnet 4.5 master + Haiku fast + Opus deep — all via "
            "OpenRouter (OPENROUTER_API_KEY required)."
        ),
        "budget": "~$0.80-10 / 1M tokens (OpenRouter rates)",
        "master":   {"provider": "openrouter", "model": "anthropic/claude-sonnet-4-5"},
        "fast":     {"provider": "openrouter", "model": "anthropic/claude-haiku-4-5"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3-0324"},
        "deep":     {"provider": "openrouter", "model": "anthropic/claude-opus-4-5"},
    },
    {
        "id": "code",
        "label": "\U0001f916 Code Specialist — For coding tasks",
        "is_default": False,
        "description": (
            "Optimized for code writing and agentic workflows. Kimi K2 as "
            "master (specialized for tool-use), Qwen Coder for code-specific "
            "workers — 30B cheap, 480B powerful."
        ),
        "budget": "~$0.07-1.55 / 1M tokens",
        "master":   {"provider": "novita", "model": "moonshotai/kimi-k2-instruct"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "qwen/qwen3-coder-30b-a3b-instruct"},
        "deep":     {"provider": "novita", "model": "qwen/qwen3-coder-480b-a35b-instruct"},
    },
    {
        "id": "hybrid",
        "label": "\U0001f500 Hybrid 70/30 — Cheap loop + premium final",
        "is_default": False,
        "description": (
            "Master model is cheap (DeepSeek V3.2) and handles ~70% of work "
            "directly. For critical final syntheses, delegates to deep worker "
            "= Claude Sonnet (premium). Result: ~30% of Sonnet cost for quality "
            "close to 100% Sonnet. NOTE: needs ANTHROPIC_API_KEY for deep slot "
            "(or use OpenRouter key)."
        ),
        "budget": "~$0.30 (loop) + Sonnet (final hard tasks)",
        "master":   {"provider": "novita",    "model": "deepseek/deepseek-v3.2"},
        "fast":     {"provider": "novita",    "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita",    "model": "meta-llama/llama-3.3-70b-instruct"},
        "deep":     {"provider": "openrouter","model": "anthropic/claude-sonnet-4-5"},
    },
    {
        "id": "speed",
        "label": "\u26a1 Speed — Minimum latency",
        "is_default": False,
        "description": (
            "When responsiveness matters more than quality. Flash variants "
            "everywhere — Ling 2.6 Flash master (~1.3s), DeepSeek V4 Flash, "
            "Llama 3.3 70B (fastest tool calling). For interactive sessions."
        ),
        "budget": "~$0.02-0.90 / 1M tokens",
        "master":   {"provider": "novita", "model": "inclusionai/ling-2.6-flash"},
        "fast":     {"provider": "novita", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "novita", "model": "deepseek/deepseek-v4-flash"},
        "deep":     {"provider": "novita", "model": "meta-llama/llama-3.3-70b-instruct"},
    },
    # ── OpenRouter-only presets (single OPENROUTER_API_KEY) ────────────────────
    {
        "id": "or_balanced",
        "label": "\U0001f310 OpenRouter: Balanced — DeepSeek V3 + Llama",
        "is_default": False,
        "description": (
            "Everything via OpenRouter. Master: DeepSeek V3 (0324) — excellent "
            "reasoning + tool-use at low cost. Workers: Llama 3.1 8B (fast), "
            "Llama 3.3 70B (balanced), DeepSeek R1 (deep reasoning). Single key."
        ),
        "budget": "~$0.07-2.00 / 1M tokens",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3-0324"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct"},
        "deep":     {"provider": "openrouter", "model": "deepseek/deepseek-r1"},
    },
    {
        "id": "or_claude",
        "label": "\U0001f310 OpenRouter: Claude Sonnet 4.5 Master",
        "is_default": False,
        "description": (
            "Master: Claude Sonnet 4.5 — top quality for complex reasoning and "
            "tool-use. Fast: Haiku 3.5. Balanced: DeepSeek V3. "
            "Deep: Claude Opus 4.5 for hardest tasks. Single OpenRouter key."
        ),
        "budget": "~$0.25-15 / 1M tokens",
        "master":   {"provider": "openrouter", "model": "anthropic/claude-sonnet-4-5"},
        "fast":     {"provider": "openrouter", "model": "anthropic/claude-haiku-3-5"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3-0324"},
        "deep":     {"provider": "openrouter", "model": "anthropic/claude-opus-4-5"},
    },
    {
        "id": "or_gemini",
        "label": "\U0001f310 OpenRouter: Gemini Pro Master",
        "is_default": False,
        "description": (
            "Master: Gemini 2.5 Pro — exceptional for long contexts and "
            "multimodal tasks. Fast: Gemini 2.0 Flash (<1s latency). "
            "Balanced: DeepSeek V3. Deep: Gemini 2.5 Pro again. Single key."
        ),
        "budget": "~$0.10-1.25 / 1M tokens",
        "master":   {"provider": "openrouter", "model": "google/gemini-2.5-pro"},
        "fast":     {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"},
        "balanced": {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3-0324"},
        "deep":     {"provider": "openrouter", "model": "google/gemini-2.5-pro"},
    },
    {
        "id": "or_reasoning",
        "label": "\U0001f310 OpenRouter: Deep Reasoning — R1 + QwQ",
        "is_default": False,
        "description": (
            "For tasks requiring chain-of-thought reasoning. Master: "
            "DeepSeek R1 (0528) — SOTA reasoning. Fast: Llama 3.1 8B. "
            "Balanced: QwQ-32B (reasoning specialist). Deep: R1-0528 again. "
            "Single OpenRouter key."
        ),
        "budget": "~$0.50-4.00 / 1M tokens",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-r1-0528"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "qwen/qwq-32b"},
        "deep":     {"provider": "openrouter", "model": "deepseek/deepseek-r1-0528"},
    },
    {
        "id": "or_china_mix",
        "label": "\U0001f1e8\U0001f1f3 OpenRouter: China Mix — MiniMax/GLM/Kimi",
        "is_default": False,
        "description": (
            "Top model from each Chinese AI family combined. Master: MiniMax M2.7 "
            "— best price/quality ratio. Fast: GLM-4.7 Flash (almost free wrap-up). "
            "Balanced: GLM-4.6. Deep: Kimi K2.6 for final syntheses with 262k context. "
            "Best starting point to test all 3 Chinese families."
        ),
        "budget": "~$0.06-3.49 / 1M tokens",
        "master":   {"provider": "openrouter", "model": "minimax/minimax-m2.7"},
        "fast":     {"provider": "openrouter", "model": "z-ai/glm-4.7-flash"},
        "balanced": {"provider": "openrouter", "model": "z-ai/glm-4.6"},
        "deep":     {"provider": "openrouter", "model": "moonshotai/kimi-k2.6"},
    },
    {
        "id": "or_eco",
        "label": "\U0001f310 OpenRouter: Eco — Minimum cost",
        "is_default": False,
        "description": (
            "Maximum savings with OpenRouter only. Master: DeepSeek V4 Flash "
            "($0.07/1M). Fast: Llama 3.1 8B (~free tier). Balanced: Qwen3 30B. "
            "Deep: DeepSeek V3 for harder cases. Single key."
        ),
        "budget": "~$0.02-0.35 / 1M tokens",
        "master":   {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"},
        "fast":     {"provider": "openrouter", "model": "meta-llama/llama-3.1-8b-instruct"},
        "balanced": {"provider": "openrouter", "model": "qwen/qwen3-30b-a3b"},
        "deep":     {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3-0324"},
    },
]


def get_preset(preset_id: str) -> Optional[dict]:
    for p in CONFIG_PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def list_presets() -> list[dict]:
    """Return preset list without revealing internal-only fields."""
    return [
        {
            "id":          p["id"],
            "label":       p["label"],
            "description": p["description"],
            "budget":      p["budget"],
            "is_default":  p["is_default"],
            "master":      p["master"],
            "fast":        p["fast"],
            "balanced":    p["balanced"],
            "deep":        p["deep"],
        }
        for p in CONFIG_PRESETS
    ]


def _resolve_provider_key(provider: str, fallback: str = "") -> str:
    """Resolve a provider's API key from environment variables."""
    import os
    env_map = {
        "novita":     "NOVITA_API_KEY",
        "groq":       "GROQ_API_KEY",
        "openai":     "OPENAI_API_KEY",
        "gemini":     "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic":  "ANTHROPIC_API_KEY",
    }
    env_name = env_map.get(provider, "")
    if not env_name:
        return fallback
    return os.environ.get(env_name, fallback)


def apply_preset(preset_id: str, master_api_key: str = "") -> dict:
    """
    Apply a preset: write master config + 3 worker slot configs to the store.

    Each slot gets its own provider-specific API key resolved from env,
    so cross-provider presets (e.g. Hybrid: novita master + anthropic deep)
    work without manual config per slot.

    Returns dict with applied config summary + warnings for missing keys.
    """
    from .llm_config import set_config
    from .workers import set_worker_slot

    preset = get_preset(preset_id)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_id}")

    master          = preset["master"]
    master_provider = master["provider"]
    master_key      = (master_api_key or "").strip() or _resolve_provider_key(master_provider)

    if not master_key:
        raise ValueError(
            f"Preset '{preset_id}' requires an API key for master provider "
            f"'{master_provider}' — neither provided nor found in env."
        )

    set_config(model=master["model"], api_key=master_key, provider=master_provider)

    warnings: list[str] = []
    for slot in ("fast", "balanced", "deep"):
        sc            = preset[slot]
        slot_provider = sc["provider"]
        slot_key      = master_key if slot_provider == master_provider else _resolve_provider_key(slot_provider)
        if not slot_key:
            warnings.append(
                f"Worker '{slot}' ({slot_provider}/{sc['model']}) has no "
                f"API key in env — slot will fail when used. Set it manually."
            )
        set_worker_slot(slot=slot, model=sc["model"], provider=slot_provider,
                        api_key=slot_key, enabled=True)

    return {
        "ok":       True,
        "preset_id": preset_id,
        "label":    preset["label"],
        "master":   master,
        "fast":     preset["fast"],
        "balanced": preset["balanced"],
        "deep":     preset["deep"],
        "warnings": warnings,
    }
