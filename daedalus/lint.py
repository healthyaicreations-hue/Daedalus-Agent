"""Daedalus Lint Gate — proactive ruff check on proposed Python code.

Runs BEFORE the sandbox in the proposal pipeline.
F-codes (pyflakes real bugs) → BLOCK proposal.
E/W-codes (style) → WARN, stored on proposal for visibility.

API:
  lint_content(file_path, content) -> LintResult
  lint_file(file_path)             -> LintResult
  format_result(result)            -> str
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_BLOCKING_PREFIXES = ("F",)
_WARN_PREFIXES = ("E", "W")
_MAX_REPORT = 20


@dataclass
class LintIssue:
    code: str
    row: int
    col: int
    message: str
    severity: str = "warning"

    @property
    def is_blocking(self) -> bool:
        return any(self.code.startswith(p) for p in _BLOCKING_PREFIXES)

    def short(self) -> str:
        return f"  L{self.row}:{self.col}  [{self.code}]  {self.message}"


@dataclass
class LintResult:
    ok: bool
    issues: list[LintIssue] = field(default_factory=list)
    blockers: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)
    file_path: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "file_path": self.file_path,
            "blocker_count": len(self.blockers),
            "warning_count": len(self.warnings),
            "blockers": [{"code": i.code, "row": i.row, "col": i.col, "msg": i.message}
                         for i in self.blockers[:_MAX_REPORT]],
            "warnings": [{"code": i.code, "row": i.row, "col": i.col, "msg": i.message}
                         for i in self.warnings[:_MAX_REPORT]],
            "error": self.error,
        }


def _is_python(file_path: str) -> bool:
    return Path(file_path).suffix in (".py", ".pyw")


def _run_ruff(path: str) -> tuple[list[LintIssue], str]:
    cmd = [sys.executable, "-m", "ruff", "check",
           "--select", "F,E,W", "--output-format", "json", "--quiet", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        raw = result.stdout.strip()
        if not raw:
            return [], ""
        data = json.loads(raw)
        issues: list[LintIssue] = []
        for item in data:
            code = item.get("code") or item.get("message", "")[:10]
            loc = item.get("location", {})
            issues.append(LintIssue(
                code=code,
                row=loc.get("row", 0),
                col=loc.get("column", 0),
                message=item.get("message", ""),
                severity=item.get("severity", "warning"),
            ))
        return issues, ""
    except subprocess.TimeoutExpired:
        return [], "ruff timeout after 15s"
    except json.JSONDecodeError as exc:
        return [], f"ruff output parse error: {exc}"
    except FileNotFoundError:
        return [], "ruff not installed (pip install ruff)"
    except Exception as exc:
        return [], f"ruff failed: {exc}"


def _build_result(issues: list[LintIssue], file_path: str, error: str) -> LintResult:
    blockers = [i for i in issues if i.is_blocking]
    warnings = [i for i in issues if not i.is_blocking]
    return LintResult(
        ok=len(blockers) == 0 and not error,
        issues=issues,
        blockers=blockers,
        warnings=warnings,
        file_path=file_path,
        error=error,
    )


def lint_content(file_path: str, content: str) -> LintResult:
    """Lint arbitrary string content as if it were file_path.

    Non-Python files always return ok=True (no-op).
    """
    if not _is_python(file_path):
        return LintResult(ok=True, file_path=file_path)
    suffix = Path(file_path).suffix or ".py"
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        issues, error = _run_ruff(tmp_path)
        return _build_result(issues, file_path, error)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def lint_file(file_path: str) -> LintResult:
    """Lint an existing file on disk."""
    p = Path(file_path)
    if not p.exists():
        return LintResult(ok=False, file_path=file_path, error=f"file not found: {file_path}")
    if not _is_python(file_path):
        return LintResult(ok=True, file_path=file_path)
    issues, error = _run_ruff(str(p))
    return _build_result(issues, file_path, error)


def format_result(result: LintResult) -> str:
    """Render LintResult as a readable string."""
    lines: list[str] = []
    fp = result.file_path or "?"
    if result.error:
        return f"⚠ ruff error: {result.error}"
    if result.ok and not result.warnings:
        return f"✅ Lint OK — {fp} — no issues"
    if result.blockers:
        lines.append(f"🚫 LINT BLOCKERS ({len(result.blockers)}) — {fp}")
        lines.append("   (These errors BLOCK the proposal — fix them first)")
        for i in result.blockers[:_MAX_REPORT]:
            lines.append(i.short())
    if result.warnings:
        lines.append(f"⚠ Lint warnings ({len(result.warnings)}) — {fp}")
        for i in result.warnings[:_MAX_REPORT]:
            lines.append(i.short())
    if not result.blockers:
        lines.append("✅ No blockers — proposal allowed (warnings only)")
    return "\n".join(lines)
