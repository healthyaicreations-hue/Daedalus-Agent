"""LLM configuration for Daedalus — runtime-switchable model and API key.

Priority (highest to lowest):
1. Persistent store override  (set via config API — survives restarts)
2. Environment variables      DAEDALUS_API_KEY / DAEDALUS_BASE_URL / DAEDALUS_MODEL
3. External providers auto    OPENROUTER_API_KEY → OpenRouter  |  NOVITA_API_KEY → Novita
                              ANTHROPIC_API_KEY → Anthropic   |  OPENAI_API_KEY → OpenAI

Supported providers (all via OpenAI-compatible SDK except native Anthropic):
  anthropic  — api.anthropic.com  (native Anthropic SDK)
  openai     — api.openai.com
  groq       — api.groq.com/openai/v1
  gemini     — generativelanguage.googleapis.com/v1beta/openai
  novita     — api.novita.ai/v3/openai
  openrouter — openrouter.ai/api/v1  (300+ models, one key)
  custom     — any base_url you supply
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

_STORE_KEY = "daedalus:llm_config"

PROVIDER_DEFAULTS: dict[str, dict] = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-5",
        "sdk": "anthropic",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "sdk": "openai",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "sdk": "openai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
        "sdk": "openai",
    },
    "novita": {
        "base_url": "https://api.novita.ai/v3/openai",
        "default_model": "deepseek/deepseek-v3.2",
        "sdk": "openai",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "deepseek/deepseek-v3.2",
        "sdk": "openai",
    },
}

PRESET_MODELS = [
    # Anthropic direct
    {"id": "claude-sonnet-4-5",            "label": "Claude Sonnet 4.5",               "provider": "anthropic",    "tier": "fast"},
    {"id": "claude-haiku-3-5",             "label": "Claude Haiku 3.5",                "provider": "anthropic",    "tier": "cheap"},
    {"id": "claude-opus-4-5",              "label": "Claude Opus 4.5",                 "provider": "anthropic",    "tier": "power"},
    # OpenAI direct
    {"id": "gpt-4o",                       "label": "GPT-4o",                           "provider": "openai",       "tier": "fast"},
    {"id": "gpt-4o-mini",                  "label": "GPT-4o mini",                      "provider": "openai",       "tier": "cheap"},
    # Groq (free tier available)
    {"id": "llama-3.3-70b-versatile",      "label": "Llama 3.3 70B (Groq)",             "provider": "groq",         "tier": "cheap"},
    # Novita AI — open-source, pay-per-token, OpenAI-compat
    {"id": "deepseek/deepseek-v3.2",               "label": "DeepSeek V3.2 (Novita) \U0001f4b0",          "provider": "novita", "tier": "fast"},
    {"id": "deepseek/deepseek-r1-0528",            "label": "DeepSeek R1 0528 (Novita) \U0001f9e0",       "provider": "novita", "tier": "power"},
    {"id": "deepseek/deepseek-v4-flash",           "label": "DeepSeek V4 Flash (Novita) \u26a1",          "provider": "novita", "tier": "fast"},
    {"id": "qwen/qwen3-235b-a22b-instruct-2507",   "label": "Qwen3 235B Instruct (Novita) \U0001f4b0",   "provider": "novita", "tier": "fast"},
    {"id": "qwen/qwen3-coder-480b-a35b-instruct",  "label": "Qwen3 Coder 480B (Novita) \u26a1",          "provider": "novita", "tier": "power"},
    {"id": "qwen/qwen3-coder-30b-a3b-instruct",    "label": "Qwen3 Coder 30B (Novita) \U0001f4b0",       "provider": "novita", "tier": "cheap"},
    {"id": "meta-llama/llama-3.3-70b-instruct",    "label": "Llama 3.3 70B (Novita) \u26a1 tools",       "provider": "novita", "tier": "fast"},
    {"id": "meta-llama/llama-3.1-8b-instruct",     "label": "Llama 3.1 8B (Novita) \U0001f195 wrap-up",  "provider": "novita", "tier": "cheap"},
    {"id": "moonshotai/kimi-k2-instruct",          "label": "Kimi K2 (Novita) \U0001f916 code/agents",   "provider": "novita", "tier": "fast"},
    {"id": "inclusionai/ling-2.6-flash",           "label": "Ling 2.6 Flash (Novita) \U0001f4a8 cheap",  "provider": "novita", "tier": "cheap"},
    # OpenRouter — 300+ models with one key
    {"id": "deepseek/deepseek-v3.2",               "label": "DeepSeek V3.2 (OpenRouter) \U0001f4b0",     "provider": "openrouter", "tier": "fast"},
    {"id": "deepseek/deepseek-r1-0528",            "label": "DeepSeek R1 0528 (OpenRouter) \U0001f9e0",  "provider": "openrouter", "tier": "power"},
    {"id": "google/gemini-2.5-flash",              "label": "Gemini 2.5 Flash (OpenRouter) \u26a1",      "provider": "openrouter", "tier": "fast"},
    {"id": "google/gemini-2.5-pro",                "label": "Gemini 2.5 Pro (OpenRouter)",               "provider": "openrouter", "tier": "power"},
    {"id": "anthropic/claude-sonnet-4-5",          "label": "Claude Sonnet 4.5 (OpenRouter)",            "provider": "openrouter", "tier": "fast"},
    {"id": "anthropic/claude-opus-4-5",            "label": "Claude Opus 4.5 (OpenRouter)",              "provider": "openrouter", "tier": "power"},
    {"id": "meta-llama/llama-3.3-70b-instruct",    "label": "Llama 3.3 70B (OpenRouter) \U0001f195",     "provider": "openrouter", "tier": "cheap"},
    {"id": "moonshotai/kimi-k2",                   "label": "Kimi K2 (OpenRouter) \U0001f916",           "provider": "openrouter", "tier": "fast"},
    {"id": "minimax/minimax-m2.7",                 "label": "MiniMax M2.7 (OpenRouter)",                 "provider": "openrouter", "tier": "fast"},
    {"id": "minimax/minimax-m2.7",                 "label": "MiniMax M2.7 (OpenRouter)",                 "provider": "openrouter", "tier": "fast"},
    {"id": "z-ai/glm-5.1",                         "label": "GLM 5.1 (OpenRouter)",                      "provider": "openrouter", "tier": "fast"},
    {"id": "moonshotai/kimi-k2.6",                 "label": "Kimi K2.6 (OpenRouter) 262k ctx",           "provider": "openrouter", "tier": "fast"},
]


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
        log.warning("llm_config: cannot write store: %s", exc)


def get_config() -> dict:
    """Return resolved LLM config: {provider, model, api_key, base_url, sdk, use_cache}."""
    store_cfg = _read_store()

    _PROVIDER_ENV_KEYS: dict[str, str] = {
        "novita":     "NOVITA_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq":       "GROQ_API_KEY",
        "openai":     "OPENAI_API_KEY",
        "anthropic":  "ANTHROPIC_API_KEY",
        "gemini":     "GEMINI_API_KEY",
    }

    # 1. Persistent store override
    if store_cfg.get("model"):
        provider = store_cfg.get("provider", "anthropic")
        pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
        api_key = store_cfg.get("api_key") or os.environ.get(_PROVIDER_ENV_KEYS.get(provider, ""), "")
        if api_key:
            return {
                "provider":  provider,
                "model":     store_cfg["model"],
                "api_key":   api_key,
                "base_url":  store_cfg.get("base_url") or pd["base_url"],
                "sdk":       pd["sdk"],
                "use_cache": store_cfg.get("use_cache", provider == "anthropic"),
                "source":    "store",
            }

    # 2. Env var overrides (DAEDALUS_*)
    env_key   = os.environ.get("DAEDALUS_API_KEY")
    env_url   = os.environ.get("DAEDALUS_BASE_URL")
    env_model = os.environ.get("DAEDALUS_MODEL")
    if env_key and env_model:
        provider = os.environ.get("DAEDALUS_PROVIDER", "anthropic")
        pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
        return {
            "provider":  provider,
            "model":     env_model,
            "api_key":   env_key,
            "base_url":  env_url or pd["base_url"],
            "sdk":       pd["sdk"],
            "use_cache": provider == "anthropic",
            "source":    "env",
        }

    # 3. Auto-detect from known provider env vars (priority order)
    for provider, env_var in [
        ("openrouter", "OPENROUTER_API_KEY"),
        ("novita",     "NOVITA_API_KEY"),
        ("anthropic",  "ANTHROPIC_API_KEY"),
        ("openai",     "OPENAI_API_KEY"),
        ("groq",       "GROQ_API_KEY"),
        ("gemini",     "GEMINI_API_KEY"),
    ]:
        key = os.environ.get(env_var, "")
        if key:
            pd = PROVIDER_DEFAULTS[provider]
            return {
                "provider":  provider,
                "model":     pd["default_model"],
                "api_key":   key,
                "base_url":  pd["base_url"],
                "sdk":       pd["sdk"],
                "use_cache": provider == "anthropic",
                "source":    f"{provider}_env",
            }

    raise RuntimeError(
        "No LLM provider configured. Set one of: OPENROUTER_API_KEY, NOVITA_API_KEY, "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY — "
        "or use DAEDALUS_API_KEY + DAEDALUS_MODEL + DAEDALUS_PROVIDER."
    )


def set_config(model: str, api_key: str, provider: str = "anthropic",
               base_url: str = "", use_cache: bool = True) -> dict:
    """Persist LLM config to store. Returns the saved config."""
    pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
    cfg = {
        "model":     model,
        "api_key":   api_key,
        "provider":  provider,
        "base_url":  base_url or pd["base_url"],
        "use_cache": use_cache,
    }
    _write_store(cfg)
    return cfg


def clear_config() -> None:
    """Remove store override — fall back to env vars."""
    try:
        from .storage import kv_del
        kv_del(_STORE_KEY)
    except Exception:
        pass
