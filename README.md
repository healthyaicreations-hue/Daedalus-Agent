# Daedalus Agent Framework

A **named, persistent coding agent** you can embed in any Python project.

Daedalus is not a stateless chatbot. It is a single continuous agent with:
- **Persistent memory** — ChromaDB semantic store, hierarchical compression across sessions
- **Multi-model worker system** — parallel specialist agents (fast/balanced/deep/coder/planner/reviewer)
- **TDD loop** — autonomous test → lint → LLM fix → repeat → proposal cycle
- **Smart model routing** — auto-selects the best model for each task from a registry
- **Preset library** — one-call config for 12 curated master+worker combinations
- **Portable persistence** — JSON file backend (no cloud DB required)

---

## Quick Start

```bash
pip install daedalus-agent
```

Set at least one provider key:

```bash
export OPENROUTER_API_KEY=sk-...   # or NOVITA_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
```

```python
import asyncio
from daedalus.workers import parse_worker_markers, call_workers_parallel, format_worker_results

async def main():
    raw = "<NEED_WORKER: balanced | Analyze the tradeoffs of using Redis vs Memcached>"
    calls = parse_worker_markers(raw)
    results = await call_workers_parallel(calls)
    print(format_worker_results(results))

asyncio.run(main())
```

---

## Configuration

### Auto-detect (zero config)

Set any provider key — Daedalus picks it up automatically:

| Env var | Provider | Default model |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter (300+ models) | deepseek/deepseek-v3.2 |
| `NOVITA_API_KEY` | Novita AI | deepseek/deepseek-v3.2 |
| `ANTHROPIC_API_KEY` | Anthropic | claude-sonnet-4-5 |
| `OPENAI_API_KEY` | OpenAI | gpt-4o |
| `GROQ_API_KEY` | Groq | llama-3.3-70b-versatile |
| `GEMINI_API_KEY` | Google Gemini | gemini-2.0-flash |

### Apply a preset (master + 3 worker slots in one call)

```python
from daedalus.llm_presets import apply_preset

# Apply the balanced preset (DeepSeek V3.2 master, Llama workers)
apply_preset("balanced")

# Apply premium (Claude Sonnet master + Opus deep) — needs OPENROUTER_API_KEY
apply_preset("or_claude")
```

Available presets: `balanced`, `eco`, `premium`, `code`, `speed`, `hybrid`,
`or_balanced`, `or_claude`, `or_gemini`, `or_reasoning`, `or_china_mix`, `or_eco`

### Browse the preset library with radar scores

```python
from daedalus.preset_library import library

for p in library():
    print(p["label"], p["scores"])
    # {'speed': 82, 'quality': 78, 'economy': 71, 'reasoning': 68, 'code': 83, 'context': 55}
```

---

## Worker System

Workers are **stateless** specialist agents. The master Daedalus emits
`<NEED_WORKER: slot | task>` markers; the framework executes them in parallel.

### Slots

| Slot | Role | Default model |
|---|---|---|
| `fast` | Quick extraction, classification, summaries | Gemini 2.5 Flash |
| `balanced` | Analysis, research, writing | Llama 3.3 70B |
| `deep` | Code writing, complex synthesis | DeepSeek V3.2 |
| `analysis` | Chain-of-thought reasoning (slow, 60-100s) | DeepSeek R1-0528 |
| `planner` | Task decomposition only (no execution) | DeepSeek R1-0528 |
| `coder` | Production-ready code writing | Qwen3 Coder 30B |
| `reviewer` | APPROVED / NEEDS_FIX / BLOCKED verdicts | Claude Sonnet 4.5 |
| `auto` | Registry-driven selection by task content | — |

### Configure a slot

```python
from daedalus.workers import set_worker_slot

set_worker_slot(
    slot="deep",
    model="anthropic/claude-opus-4-5",
    provider="openrouter",
)
```

---

## Model Registry

The registry tracks all available models with capability tags, cost, latency,
and success rates. It powers the `auto` slot.

```python
from daedalus.model_registry import get_registry, select_for_task, set_auto_mode

# Enable auto-routing
set_auto_mode(True)

# Find the best model for a task
best = select_for_task("write a FastAPI endpoint with tests", budget_hint="cheap")
print(best["display_name"])  # e.g. "Qwen3 Coder 30B (Novita)"

# Inspect the registry
for model in get_registry():
    print(model["display_name"], model["tier"], model["cost_input"])
```

---

## TDD Loop

The TDD loop runs autonomously: write a test → lint → run → LLM fix → repeat.

```python
import asyncio
from daedalus.tdd import tdd_loop

async def main():
    result = await tdd_loop(
        file_path="my_module.py",
        test_code="""
from my_module import add
assert add(2, 3) == 5
assert add(-1, 1) == 0
""",
        initial_code="""
def add(a, b):
    return a - b  # bug: should be +
""",
        max_iterations=5,
    )
    print(result.format_for_daedalus())

asyncio.run(main())
```

---

## Lint Gate

```python
from daedalus.lint import lint_content, format_for_daedalus

result = lint_content("my_file.py", open("my_file.py").read())
print(format_for_daedalus(result))
```

F-codes (real bugs) → block proposal. E/W codes → warn only.

---

## Persona / Identity

Daedalus has a rich system prompt that you can customize:

```python
from daedalus.identity import build_persona

persona = build_persona(
    owner_name="Alice",
    project_name="MyAwesomeApp",
    language="English",
    extra_context="This app is a medical records system. Be extra careful with data integrity.",
)
```

Or set via environment:
```bash
export DAEDALUS_OWNER_NAME="Alice"
export DAEDALUS_PROJECT_NAME="MyAwesomeApp"
export DAEDALUS_LANGUAGE="English"
```

---

## Storage

All config is persisted to `~/.daedalus/store.json` by default.
Override with `DAEDALUS_STORE_PATH`.

```python
from daedalus.storage import kv_get, kv_set, kv_del, kv_keys

kv_set("my:key", {"data": 42})
print(kv_get("my:key"))  # {"data": 42}
```

---

## Architecture

```
daedalus/
├── identity.py        — Persona (DAEDALUS_PERSONA system prompt, build_persona())
├── storage.py         — Portable KV store (JSON file, thread-safe, _DB shim)
├── llm_config.py      — Provider config (auto-detect, set_config, PRESET_MODELS)
├── llm_presets.py     — 12 curated master+worker presets (apply_preset)
├── preset_library.py  — Radar scores + curated library view (library())
├── model_registry.py  — Model profiles + smart selection (select_for_task)
├── workers.py         — Parallel worker system (call_workers_parallel, WORKER_PERSONAS)
├── tdd.py             — Autonomous TDD loop (tdd_loop, parse_tdd_marker)
├── lint.py            — Ruff-based lint gate (lint_content, lint_file)
├── agent.py           — Master agent loop (tool parsing, memory, proposals)
├── proposals.py       — Proposal system (propose_patch, approve/reject)
└── sandbox.py         — Restricted Python sandbox (run_snippet)
```

---

## Install from source

```bash
git clone https://github.com/healthyaicreations-hue/Daedalus-Agent.git
cd Daedalus-Agent
pip install -e ".[dev]"
```

---

## License

MIT
