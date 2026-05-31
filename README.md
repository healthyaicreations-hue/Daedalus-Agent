# Daedalus-Agent

**Autonomous coding agent framework** — write a test, watch it fix the code itself.

Daedalus is a lightweight Python framework for building autonomous coding agents. Its core feature is a **closed TDD loop**: the agent writes a test, runs it in an isolated sandbox, sends failures to an LLM worker for fixing, and iterates — without asking for permission at every step. When the tests pass, it creates a proposal for human review.

```
Test fails → LLM fixes → Lint check → Run again → ... → Tests pass → Proposal
```

Works with any OpenAI-compatible API: **OpenRouter, Anthropic, Ollama (local), Groq, Novita**.

---

## Features

| Feature | Description |
|---|---|
| **TDD Loop** | Autonomous test → fix → iterate cycle (up to N iterations, no interruptions) |
| **Sandbox** | 5-layer isolated Python execution (syntax → import → run → lint → security) |
| **Lint Gate** | ruff F-codes block proposals before they reach the sandbox |
| **Parallel Workers** | Multiple LLM slots (fast / balanced / deep / coder / reviewer) running in parallel |
| **Proposals** | Human-in-the-loop approval gate — agent proposes, you approve or reject |
| **Pluggable Storage** | SQLite (default), JSON file, or in-memory — no external services required |
| **CLI** | `daedalus run`, `daedalus tdd`, `daedalus lint`, `daedalus proposals` |
| **FastAPI ready** | Drop-in HTTP service example included |

---

## Quick Start

### 1. Install

```bash
pip install daedalus-agent
```

Or from source:

```bash
git clone https://github.com/Blaue-Ente/Daedalus-Agent.git
cd Daedalus-Agent
pip install -e ".[all]"
```

**Minimum dependencies** (installed automatically):
- `openai>=1.30.0` — for OpenAI-compatible API calls (works with OpenRouter, Ollama, Groq, etc.)
- `ruff>=0.4.0` — for the lint gate

**Optional extras:**
```bash
pip install daedalus-agent[anthropic]   # Anthropic SDK (for direct Anthropic API)
pip install daedalus-agent[security]    # bandit (security scanning)
pip install daedalus-agent[yaml]        # PyYAML (for config.yml)
pip install daedalus-agent[all]         # everything
```

### 2. Set API Key

```bash
export OPENROUTER_API_KEY="sk-or-..."   # OpenRouter (recommended — access to 100+ models)
# OR
export ANTHROPIC_API_KEY="sk-ant-..."  # Anthropic direct
# OR use Ollama locally — no key needed (see below)
```

### 3. Run

**Option A — CLI:**
```bash
# Interactive agent
daedalus run "Write a binary search function with tests in search.py"

# TDD loop directly
daedalus tdd mymodule.py tests/test_mymodule.py

# Lint a file
daedalus lint mymodule.py

# Review and approve pending proposals
daedalus proposals
daedalus proposals --approve abc12345
```

**Option B — Python API:**
```python
import asyncio
from daedalus import Agent

async def main():
    agent = Agent.from_config("config.yml")

    result = await agent.run(
        "Write a function that validates email addresses. "
        "Use NEED_TDD to test it in validator.py"
    )
    print(result.response)

asyncio.run(main())
```

**Option C — TDD loop standalone:**
```python
import asyncio
from pathlib import Path
from daedalus.tdd import tdd_loop

TEST_CODE = """
from mymodule import add
assert add(2, 3) == 5
assert add(-1, 1) == 0
assert add(0, 0) == 0
"""

BROKEN_CODE = """
def add(a, b):
    return a - b  # bug
"""

async def main():
    result = await tdd_loop(
        file_path="mymodule.py",
        test_code=TEST_CODE,
        initial_code=BROKEN_CODE,
    )
    print(result.format())
    if result.ok:
        Path("mymodule.py").write_text(result.final_code)

asyncio.run(main())
```

---

## How It Works

### TDD Loop

```
                    ┌─────────────────────────────────┐
User provides:      │  file_path + test_code           │
                    └──────────────┬──────────────────┘
                                   ▼
                    ┌─────────────────────────────────┐
Iteration 1..N:     │  1. Lint check (ruff F-codes)    │
                    │     F-error? → send to LLM fixer │
                    │  2. Run test in sandbox           │
                    │     PASS? → create Proposal ✅    │
                    │     FAIL? → send to LLM fixer     │
                    │  3. LLM "coder" worker fixes code  │
                    │  4. Repeat                         │
                    └──────────────┬──────────────────┘
                                   ▼
                    ┌─────────────────────────────────┐
                    │  Proposal (pending human review)  │
                    │  daedalus proposals --approve ID  │
                    └─────────────────────────────────┘
```

The loop runs **fully autonomously** — no prompts to the user between iterations. You only see the final proposal.

### Sandbox Layers

Every proposed code change passes through 5 layers:

| Layer | What it checks | Speed |
|---|---|---|
| 1. Syntax | `compile()` — instant AST parse | <1ms |
| 2. Import | subprocess import — catches circular imports, missing deps | ~200ms |
| 3. Snippet | exec() in isolated subprocess — runs test assertions | ~500ms |
| 4. Lint | ruff F/E/W — undefined names, unused imports, style | ~100ms |
| 5. Security | bandit MEDIUM/HIGH (optional) | ~1s |

Layers 1–4 run by default. Security scan is opt-in (`run_security=True`).

### Proposal System

Daedalus never writes to disk without permission. Every code change becomes a **Proposal**:

```python
from daedalus.proposals import ProposalStore

store = ProposalStore()

# Create (with lint + sandbox validation)
result = store.validate_and_create(
    file_path="mymodule.py",
    new_content=new_code,
    reason="TDD loop: add() function fixed",
    test_code=test_code,
)
# → {"ok": True, "proposal_id": "abc12345"}

# Review via CLI:
# daedalus proposals
# daedalus proposals --approve abc12345

# Or programmatically:
store.approve("abc12345")   # writes to disk
store.reject("abc12345", reason="Approach incorrect")
```

### Parallel Workers

Workers are stateless LLM calls with specialized personas:

```python
from daedalus.workers import call_workers_parallel

results = await call_workers_parallel([
    {"slot": "coder",    "task": "Implement a binary search in Python"},
    {"slot": "reviewer", "task": "Review: def foo(): return None"},
    {"slot": "fast",     "task": "Summarize: <long text>"},
])
# All three run in parallel via asyncio.gather()
```

Available slots: `fast`, `balanced`, `deep`, `coder`, `reviewer`

---

## Configuration

Copy `config.example.yml` and set your API keys:

```bash
cp config.example.yml config.yml
```

```yaml
agent:
  model: "deepseek/deepseek-v3.2"
  provider: openrouter
  api_key: "${OPENROUTER_API_KEY}"
  max_tokens: 8192
  max_tool_iterations: 15

workers:
  fast:
    model: "google/gemini-2.5-flash"
    provider: openrouter
    api_key: "${OPENROUTER_API_KEY}"
  coder:
    model: "deepseek/deepseek-v3.2"
    provider: openrouter
    api_key: "${OPENROUTER_API_KEY}"
```

Or use environment variables (no config file needed):

```bash
export OPENROUTER_API_KEY="sk-or-..."
export DAEDALUS_MODEL="deepseek/deepseek-v3.2"         # optional
export DAEDALUS_CODER_MODEL="deepseek/deepseek-v3.2"   # optional
```

### Using Local Models (Ollama)

No API key or internet required:

```bash
# Install Ollama: https://ollama.com
ollama pull qwen2.5-coder:14b

export DAEDALUS_PROVIDER=ollama
export DAEDALUS_BASE_URL=http://localhost:11434/v1
export DAEDALUS_MODEL=qwen2.5-coder:14b
export OPENROUTER_API_KEY=ollama   # dummy key required by openai SDK
```

---

## Agent Markers

When integrated in the full agent, the LLM communicates via markers:

| Marker | Format | Effect |
|---|---|---|
| `NEED_SANDBOX` | `<NEED_SANDBOX: python_code>` | Run code in isolated subprocess |
| `NEED_LINT` | `<NEED_LINT: path/to/file.py>` | Check file with ruff |
| `NEED_TDD` | `<NEED_TDD: file.py \| test_code>` | Run full TDD loop autonomously |
| `NEED_WORKER` | `<NEED_WORKER: slot \| task>` | Parallel LLM worker call |
| `NEED_READ` | `<NEED_READ: path/to/file>` | Read file from disk |
| `NEED_WRITE` | `<NEED_WRITE: path \| content>` | Write file to disk |

Multiple `NEED_WORKER` markers in one response run in **parallel**.

---

## Project Structure

```
Daedalus-Agent/
├── daedalus/
│   ├── __init__.py      # Public API
│   ├── agent.py         # Tool loop orchestrator
│   ├── sandbox.py       # Isolated Python execution (5 layers)
│   ├── lint.py          # ruff lint gate (F/E/W)
│   ├── tdd.py           # Autonomous TDD loop
│   ├── workers.py       # Parallel LLM worker slots
│   ├── proposals.py     # Code change proposal system
│   ├── storage.py       # Pluggable storage (SQLite/JSON/memory)
│   └── __main__.py      # CLI entry point
├── examples/
│   ├── simple_coding_agent/  # Full agent example
│   ├── tdd_standalone/       # TDD loop without full agent
│   └── fastapi_integration/  # HTTP service
├── config.example.yml   # Config template
├── pyproject.toml
└── README.md
```

---

## Requirements

- **Python 3.11+**
- An OpenAI-compatible API key (OpenRouter, Anthropic, Groq, Ollama...)
- `ruff` — installed automatically

**Tested models:**
- `deepseek/deepseek-v3.2` (via OpenRouter) — recommended for coder slot
- `google/gemini-2.5-flash` (via OpenRouter) — recommended for fast slot
- `anthropic/claude-sonnet-4-5` (via OpenRouter or direct) — recommended for reviewer
- `qwen2.5-coder:14b` (via Ollama) — best local model for coding tasks

---

## Examples

Run the examples:

```bash
cd examples/tdd_standalone
export OPENROUTER_API_KEY="your-key"
python run.py
```

```bash
cd examples/fastapi_integration
pip install fastapi uvicorn
uvicorn main:app --reload
# → http://localhost:8000/docs
```

---

## License

MIT — see [LICENSE](LICENSE)

---

## Origin

Daedalus-Agent is extracted from [ArgosDataNexus](https://github.com/Blaue-Ente), where it runs as a persistent engineering agent with 186+ knowledge chunks, PostgreSQL memory, and a full admin UI. This repo contains the portable, dependency-free core.
