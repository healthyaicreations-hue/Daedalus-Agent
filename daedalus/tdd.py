"""Daedalus TDD Loop — autonomous test-driven development cycle.

Workflow:
  1. Caller provides: file_path, test_code, initial_code (optional)
  2. tdd_loop() runs autonomously — NO human interruptions between iterations:
       a. Lint check (ruff F-codes)
       b. Run test_code + current_code in sandbox
       c. If FAIL → LLM "coder" worker fixes the code
       d. Repeat until PASS or max_iterations reached
  3. On success → returns TDDResult(ok=True) with final code
  4. On failure → returns TDDResult(ok=False) with full iteration log

The caller decides what to do with TDDResult.final_code
(write to disk, create a proposal, notify a human, etc.)
"""
from __future__ import annotations

import asyncio
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MAX_ITER = 5
MAX_CODE_CHARS = 30_000
MAX_TEST_CHARS = 8_000


_FIX_SYSTEM = (
    "You are a senior Python engineer. Fix the given Python code so the test suite passes.\n"
    "Return ONLY the complete fixed Python source — no explanations, no markdown fences.\n"
    "If the fix is impossible, return the original code with a comment at the top:\n"
    "# TDD_UNFIXABLE: <reason>"
)


def _build_fix_prompt(
    file_path: str, test_code: str, current_code: str, error_msg: str, iteration: int
) -> str:
    return textwrap.dedent(f"""\
        File: {file_path}
        Iteration: {iteration}/{MAX_ITER}

        === TEST CODE (must pass) ===
        {test_code[:MAX_TEST_CHARS]}

        === CURRENT CODE (failing) ===
        {current_code[:MAX_CODE_CHARS]}

        === FAILURE OUTPUT ===
        {error_msg[:3000]}

        Fix the code so the tests pass. Return ONLY the fixed Python source.
    """)


def _extract_code(response: str) -> str:
    """Pull Python code from LLM response — handles fenced blocks or raw code."""
    m = re.search(r"```(?:python)?\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"```\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return response.strip()


@dataclass
class TDDIteration:
    number: int
    passed: bool
    summary: str
    elapsed_ms: int


@dataclass
class TDDResult:
    ok: bool
    file_path: str
    final_code: str = ""
    iterations_used: int = 0
    iterations: list[TDDIteration] = field(default_factory=list)
    error: str = ""
    unfixable: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "file_path": self.file_path,
            "iterations_used": self.iterations_used,
            "error": self.error,
            "unfixable": self.unfixable,
            "iterations": [
                {"n": i.number, "passed": i.passed, "summary": i.summary, "ms": i.elapsed_ms}
                for i in self.iterations
            ],
        }

    def format(self) -> str:
        """Render result as a readable string."""
        lines: list[str] = []
        fp = self.file_path
        if self.ok:
            lines.append(
                f"✅ TDD PASSED — {fp}\n"
                f"   Iterations: {self.iterations_used}/{MAX_ITER}\n"
                f"   All tests passed — code is ready."
            )
        elif self.unfixable:
            lines.append(
                f"🛑 TDD UNFIXABLE — {fp}\n"
                f"   LLM marked as unfixable after {self.iterations_used} iterations.\n"
                f"   Reason: {self.error}\n"
                f"   Action: reformulate the test or change requirements."
            )
        else:
            lines.append(
                f"❌ TDD FAILED — {fp}\n"
                f"   {self.iterations_used}/{MAX_ITER} iterations without success.\n"
                f"   Last error: {self.error[:400]}\n"
                f"   Action: review the test/code, or break the task down."
            )
        for it in self.iterations:
            status = "✓" if it.passed else "✗"
            lines.append(f"   [{status}] Iteration {it.number}: {it.summary[:120]}")
        return "\n".join(lines)


async def _llm_fix(
    file_path: str,
    test_code: str,
    current_code: str,
    error_msg: str,
    iteration: int,
    worker_config=None,
) -> str:
    """Call the 'coder' worker to fix the code. Returns fixed source."""
    from .workers import call_workers_parallel
    prompt = _build_fix_prompt(file_path, test_code, current_code, error_msg, iteration)
    results = await call_workers_parallel(
        [{"slot": "coder", "task": prompt}],
        config=worker_config,
    )
    r = results[0] if results else {}
    if not r.get("ok"):
        return current_code
    raw = r.get("result", "")
    return _extract_code(raw) or current_code


async def tdd_loop(
    file_path: str,
    test_code: str,
    initial_code: str = "",
    max_iterations: int = MAX_ITER,
    worker_config=None,
    on_iteration=None,
) -> TDDResult:
    """Run the TDD loop autonomously.

    Args:
        file_path:      Target file path (for context and lint checks).
        test_code:      Test code that MUST pass (assert statements or pytest style).
        initial_code:   Starting code. If empty, reads from file_path if it exists.
        max_iterations: Max fix attempts (default: 5).
        worker_config:  WorkerConfig instance. If None, builds from env.
        on_iteration:   Optional async callback(iteration: TDDIteration) for progress reporting.

    Returns:
        TDDResult with ok=True if tests passed, ok=False otherwise.

    Example:
        result = await tdd_loop(
            file_path="mymodule.py",
            test_code='''
                from mymodule import add
                assert add(2, 3) == 5
                assert add(-1, 1) == 0
            ''',
            initial_code='''
                def add(a, b):
                    return a - b  # bug: should be +
            ''',
        )
        if result.ok:
            Path("mymodule.py").write_text(result.final_code)
        print(result.format())
    """
    from .sandbox import run_snippet
    from .lint import lint_content, format_result as fmt_lint

    if not initial_code:
        p = Path(file_path)
        initial_code = p.read_text("utf-8") if p.exists() else ""

    current_code = initial_code
    iterations: list[TDDIteration] = []

    log.info("TDD loop START: %s, max_iter=%d", file_path, max_iterations)

    for i in range(1, max_iterations + 1):
        iter_t0 = time.monotonic()

        lint_r = lint_content(file_path, current_code)
        if not lint_r.ok:
            lint_msg = fmt_lint(lint_r)
            error_for_llm = f"Lint gate blocked:\n{lint_msg}"
            elapsed_ms = int((time.monotonic() - iter_t0) * 1000)
            it = TDDIteration(
                number=i, passed=False,
                summary=f"LINT FAIL — {lint_msg[:150]}",
                elapsed_ms=elapsed_ms,
            )
            iterations.append(it)
            if on_iteration:
                await on_iteration(it)
            log.info("TDD iter %d: lint FAILED", i)
        else:
            combined = current_code + "\n\n" + test_code
            sandbox_r = run_snippet(combined, timeout=20)
            elapsed_ms = int((time.monotonic() - iter_t0) * 1000)
            passed = sandbox_r.get("ok", False)

            stdout = (sandbox_r.get("stdout") or "").strip()
            stderr = (sandbox_r.get("stderr") or "").strip()
            tb = (sandbox_r.get("traceback") or "").strip()
            error_for_llm = (
                sandbox_r.get("error", "") + "\n" + (tb or stderr or stdout)
            ).strip()

            summary = (
                f"PASS ({elapsed_ms}ms) ✓ all assertions passed"
                if passed
                else f"FAIL ({elapsed_ms}ms) {error_for_llm[:150]}"
            )
            it = TDDIteration(number=i, passed=passed, summary=summary, elapsed_ms=elapsed_ms)
            iterations.append(it)
            if on_iteration:
                await on_iteration(it)

            log.info("TDD iter %d: %s", i, summary[:100])

            if passed:
                return TDDResult(
                    ok=True,
                    file_path=file_path,
                    final_code=current_code,
                    iterations_used=i,
                    iterations=iterations,
                )

        if i < max_iterations:
            log.info("TDD iter %d: calling LLM fixer...", i)
            try:
                fixed = await _llm_fix(
                    file_path, test_code, current_code, error_for_llm, i, worker_config
                )
            except Exception as exc:
                log.warning("TDD LLM fixer error: %s", exc)
                fixed = current_code

            if "TDD_UNFIXABLE:" in fixed:
                m = re.search(r"#\s*TDD_UNFIXABLE:\s*(.+)", fixed)
                reason = m.group(1).strip() if m else "LLM marked as unfixable"
                return TDDResult(
                    ok=False, file_path=file_path, final_code=fixed,
                    iterations_used=i, iterations=iterations,
                    error=reason, unfixable=True,
                )

            if fixed and fixed != current_code:
                current_code = fixed
                log.info("TDD iter %d: code updated (%d chars)", i, len(current_code))

    last_error = iterations[-1].summary if iterations else "no iterations"
    return TDDResult(
        ok=False, file_path=file_path, final_code=current_code,
        iterations_used=max_iterations, iterations=iterations,
        error=f"Max {max_iterations} iterations. Last: {last_error}",
    )
