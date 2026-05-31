"""Daedalus Agent — tool loop orchestrator.

The agent receives a user request, detects NEED_* markers in LLM output,
executes the corresponding tools, and feeds results back into the next
LLM call. This continues until no more markers are found.

Markers:
  <NEED_SANDBOX: python_code>         — run code in sandbox
  <NEED_LINT: file_path>              — lint a file with ruff
  <NEED_TDD: file_path | test_code>   — run TDD loop
  <NEED_WORKER: slot | task>          — call a worker LLM
  <NEED_READ: file_path>              — read a file from disk
  <NEED_WRITE: file_path | content>   — write a file to disk

Usage:
  agent = Agent(config)
  result = await agent.run("Implement a fibonacci function with tests")
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 15

_MARKER_RE = re.compile(r"<NEED_\w+:")


@dataclass
class AgentConfig:
    """Configuration for the Daedalus agent."""
    model: str = "deepseek/deepseek-v3.2"
    provider: str = "openrouter"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 8192
    system_prompt: str = ""
    max_tool_iterations: int = MAX_TOOL_ITERATIONS
    storage_path: str = ".daedalus.db"
    worker_config: Any = None

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_env(cls) -> "AgentConfig":
        import os
        return cls(
            model=os.environ.get("DAEDALUS_MODEL", "deepseek/deepseek-v3.2"),
            provider=os.environ.get("DAEDALUS_PROVIDER", "openrouter"),
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=os.environ.get("DAEDALUS_BASE_URL", ""),
        )


@dataclass
class AgentRun:
    """Result of a single agent run."""
    ok: bool
    response: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    iterations: int = 0
    elapsed_ms: int = 0
    error: str = ""


_DEFAULT_SYSTEM = """\
You are Daedalus, an autonomous coding agent. You can use the following tools
by emitting special markers in your response:

<NEED_SANDBOX: python_code_here>
  Run Python code in an isolated sandbox. Returns stdout/stderr/errors.

<NEED_LINT: path/to/file.py>
  Check a file with ruff. Returns lint errors and warnings.

<NEED_TDD: path/to/file.py | test_code_here>
  Run an autonomous TDD loop. Writes test, runs it, fixes code until pass.

<NEED_WORKER: slot | task description>
  Delegate a subtask to a specialized worker (fast/balanced/deep/coder/reviewer).
  Multiple NEED_WORKER markers run in PARALLEL.

<NEED_READ: path/to/file>
  Read a file from disk.

<NEED_WRITE: path/to/file | file_content_here>
  Write content to a file on disk.

Rules:
- Always emit a marker when you need to do something — never promise without acting.
- Multiple NEED_WORKER markers in one response run in parallel — use this.
- After receiving tool results, continue reasoning and emitting more markers if needed.
- When done, provide a clear summary of what was accomplished.
"""


class Agent:
    """Daedalus autonomous coding agent."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig.from_env()
        from .storage import SqliteStorage
        from .proposals import ProposalStore
        self._storage = SqliteStorage(self.config.storage_path)
        self.proposals = ProposalStore(self._storage)
        self._system = self.config.system_prompt or _DEFAULT_SYSTEM

    @classmethod
    def from_config(cls, path: str) -> "Agent":
        """Load agent from a YAML config file."""
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        return cls(AgentConfig.from_dict(d.get("agent", {})))

    async def _llm_call(self, messages: list[dict]) -> str:
        """Make one LLM call. Returns the assistant text."""
        cfg = self.config
        api_key = cfg.api_key
        base_url = cfg.base_url
        provider = cfg.provider
        model = cfg.model

        from .workers import _PROVIDER_BASE_URLS
        if not api_key:
            import os
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not base_url:
            base_url = _PROVIDER_BASE_URLS.get(provider, _PROVIDER_BASE_URLS["openrouter"])

        if provider == "anthropic" and not cfg.base_url:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key, max_retries=1)
            sys_msgs = [m for m in messages if m["role"] == "system"]
            user_msgs = [m for m in messages if m["role"] != "system"]
            system_text = sys_msgs[0]["content"] if sys_msgs else self._system
            response = await client.messages.create(
                model=model, max_tokens=cfg.max_tokens,
                system=system_text, messages=user_msgs,
            )
            return response.content[0].text if response.content else ""
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            response = await client.chat.completions.create(
                model=model, max_tokens=cfg.max_tokens, messages=messages,
            )
            return response.choices[0].message.content or ""

    async def run(
        self,
        user_message: str,
        context: str = "",
        history: list[dict] | None = None,
    ) -> AgentRun:
        """Run the agent on a user message.

        Args:
            user_message: The user's request.
            context:      Optional additional context prepended to the system prompt.
            history:      Optional prior conversation history.

        Returns:
            AgentRun with the final response and all tool calls made.
        """
        t0 = time.monotonic()
        tool_calls: list[dict] = []
        system = (context + "\n\n" + self._system).strip() if context else self._system

        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        last_response = ""
        for iteration in range(self.config.max_tool_iterations):
            try:
                raw = await self._llm_call(messages)
            except Exception as exc:
                return AgentRun(
                    ok=False, error=str(exc), iterations=iteration,
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )

            last_response = raw
            messages.append({"role": "assistant", "content": raw})

            tool_results, calls = await self._handle_markers(raw)
            tool_calls.extend(calls)

            if not tool_results:
                break

            messages.append({"role": "user", "content": tool_results})

        return AgentRun(
            ok=True,
            response=last_response,
            tool_calls=tool_calls,
            iterations=iteration + 1,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    async def _handle_markers(self, raw: str) -> tuple[str, list[dict]]:
        """Detect and handle all NEED_* markers. Returns (tool_results_text, calls_log)."""
        if not raw or not _MARKER_RE.search(raw):
            return "", []

        parts: list[str] = []
        calls: list[dict] = []

        sandbox_q = _extract(raw, "NEED_SANDBOX")
        lint_q = _extract(raw, "NEED_LINT")
        read_q = _extract(raw, "NEED_READ")
        write_m = _extract_two(raw, "NEED_WRITE")
        tdd_m = _extract_tdd(raw)
        worker_calls = _extract_workers(raw)

        coros: list[Any] = []
        tags: list[str] = []

        if sandbox_q:
            coros.append(self._tool_sandbox(sandbox_q))
            tags.append("sandbox")
        if lint_q:
            coros.append(self._tool_lint(lint_q))
            tags.append("lint")
        if read_q:
            coros.append(self._tool_read(read_q))
            tags.append("read")
        if write_m:
            coros.append(self._tool_write(*write_m))
            tags.append("write")
        if tdd_m:
            coros.append(self._tool_tdd(**tdd_m))
            tags.append("tdd")
        if worker_calls:
            coros.append(self._tool_workers(worker_calls))
            tags.append("workers")

        if not coros:
            return "", []

        results = await asyncio.gather(*coros, return_exceptions=True)
        for tag, result in zip(tags, results):
            if isinstance(result, Exception):
                parts.append(f"━━━ TOOL [{tag.upper()}] ERROR ━━━\n{result}")
                calls.append({"tool": tag, "ok": False, "error": str(result)})
            else:
                text, meta = result
                parts.append(text)
                calls.append({"tool": tag, "ok": True, **meta})

        return "\n\n".join(parts), calls

    async def _tool_sandbox(self, code: str) -> tuple[str, dict]:
        from .sandbox import run_snippet
        r = run_snippet(code)
        if r.get("ok"):
            out = r.get("stdout", "").strip() or "(no output)"
            text = f"━━━ TOOL [SANDBOX] ✓ ━━━\n{out}"
        else:
            text = (
                f"━━━ TOOL [SANDBOX] ✗ ━━━\n"
                f"Error: {r.get('error', '?')}\n"
                f"{r.get('traceback', '')[:500]}"
            )
        return text, {"ok": r.get("ok")}

    async def _tool_lint(self, file_path: str) -> tuple[str, dict]:
        from .lint import lint_file, format_result
        r = lint_file(file_path.strip())
        return f"━━━ TOOL [LINT] ━━━\n{format_result(r)}", {"ok": r.ok, "path": file_path}

    async def _tool_read(self, file_path: str) -> tuple[str, dict]:
        path = Path(file_path.strip())
        if not path.exists():
            return f"━━━ TOOL [READ] ✗ ━━━\nFile not found: {file_path}", {"ok": False}
        content = path.read_text("utf-8")
        return f"━━━ TOOL [READ] — {file_path} ({len(content)} chars) ━━━\n{content[:8000]}", {"ok": True}

    async def _tool_write(self, file_path: str, content: str) -> tuple[str, dict]:
        path = Path(file_path.strip())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"━━━ TOOL [WRITE] ✓ ━━━\nWritten {len(content)} chars to {file_path}", {"ok": True}

    async def _tool_tdd(self, file_path: str, test_code: str, initial_code: str = "") -> tuple[str, dict]:
        from .tdd import tdd_loop
        from .workers import WorkerConfig
        wc = self.config.worker_config or WorkerConfig.from_env()
        result = await tdd_loop(
            file_path=file_path,
            test_code=test_code,
            initial_code=initial_code,
            worker_config=wc,
        )
        return f"━━━ TOOL [TDD] ━━━\n{result.format()}", result.to_dict()

    async def _tool_workers(self, worker_calls: list[dict]) -> tuple[str, dict]:
        from .workers import call_workers_parallel, format_results, WorkerConfig
        wc = self.config.worker_config or WorkerConfig.from_env()
        results = await call_workers_parallel(worker_calls, config=wc)
        return f"━━━ TOOL [WORKERS] ━━━\n{format_results(results)}", {"count": len(results)}


def _extract(raw: str, marker: str) -> str:
    m = re.search(rf"<{marker}:\s*([^>]{{1,10000}})\s*>", raw, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_two(raw: str, marker: str) -> tuple[str, str] | None:
    m = re.search(rf"<{marker}:\s*([^|>]{{1,500}})\|([^>]{{0,50000}})\s*>", raw, re.DOTALL)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def _extract_tdd(raw: str) -> dict | None:
    if "NEED_TDD" not in raw:
        return None
    m = re.search(
        r"<NEED_TDD:\s*([^|>]{1,200})\|([^|>]{10,30000})(?:\|([^>]{0,30000}))?\s*>",
        raw, re.DOTALL,
    )
    if not m:
        return None
    return {
        "file_path": m.group(1).strip(),
        "test_code": m.group(2).strip(),
        "initial_code": (m.group(3) or "").strip(),
    }


def _extract_workers(raw: str) -> list[dict]:
    if "NEED_WORKER" not in raw:
        return []
    calls = []
    for m in re.finditer(r"<NEED_WORKER:\s*([^|>]{1,80})\|([^>]{1,5000})\s*>", raw, re.DOTALL):
        calls.append({"slot": m.group(1).strip(), "task": m.group(2).strip()})
    return calls
