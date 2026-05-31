"""Daedalus Sandbox — safe isolated Python execution.

Five validation layers (fast → slow):

  1. syntax_check(code)
       compile() AST parse — instant, no I/O, no imports.
       Catches SyntaxError before anything else.

  2. import_check(file_path, code)
       Writes code to a temp file and imports it in a subprocess.
       Catches ImportError, AttributeError, circular imports.

  3. run_snippet(code, timeout)
       exec() in subprocess with stdout/stderr capture.
       For test snippets, smoke tests, assertions.

  4. run_bandit(files)  [optional — requires bandit]
       Security scan. HIGH severity = blocking.

  5. validate_patch(file_path, new_content, test_code)
       Orchestrates all layers. Returns a unified result dict.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

_SANDBOX_TIMEOUT_DEFAULT = 12
_SUBPROCESS_OVERHEAD = 3
_STATIC_TIMEOUT = 30
_TEST_TIMEOUT = 60


def syntax_check(code: str) -> dict[str, Any]:
    """Parse code with compile(). No execution, no imports.

    Returns: {ok, error?, error_type?, line?, col?}
    """
    try:
        compile(code, "<daedalus_check>", "exec")
        return {"ok": True, "checks": ["syntax"]}
    except SyntaxError as exc:
        return {
            "ok": False,
            "error_type": "SyntaxError",
            "error": str(exc),
            "line": exc.lineno,
            "col": exc.offset,
            "checks": ["syntax"],
        }
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc), "checks": ["syntax"]}


def import_check(file_path: str, code: str, timeout: int = _SANDBOX_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """Write code to a temp file and import it in a subprocess.

    Returns: {ok, stdout, stderr, error?, checks, elapsed_ms}
    """
    suffix = Path(file_path).suffix or ".py"
    with tempfile.TemporaryDirectory(prefix="daedalus_import_") as tmpdir:
        tmp = Path(tmpdir) / ("module" + suffix)
        tmp.write_text(code, encoding="utf-8")
        runner = textwrap.dedent(f"""
import sys, json
sys.path.insert(0, {repr(tmpdir)})
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("_mod", {repr(str(tmp))})
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(json.dumps({{"ok": True, "stdout": "", "stderr": ""}}))
except Exception as exc:
    import traceback
    print(json.dumps({{
        "ok": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(),
        "stdout": "",
        "stderr": "",
    }}))
""")
        return _run_subprocess(runner, timeout + _SUBPROCESS_OVERHEAD, checks=["syntax", "import"])


def run_snippet(code: str, timeout: int = _SANDBOX_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """Execute arbitrary Python code in an isolated subprocess.

    stdout and stderr are captured. Hard timeout enforced.

    Returns: {ok, stdout, stderr, error?, error_type?, traceback?, checks, elapsed_ms}
    """
    runner = textwrap.dedent(f"""
import sys, json, io, traceback
_out = io.StringIO()
_err = io.StringIO()
sys.stdout = _out
sys.stderr = _err
try:
    exec(compile({repr(code)}, '<daedalus_snippet>', 'exec'), {{}})
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    print(json.dumps({{"ok": True, "stdout": _out.getvalue(), "stderr": _err.getvalue()}}))
except Exception as exc:
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    print(json.dumps({{
        "ok": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exc(),
        "stdout": _out.getvalue(),
        "stderr": _err.getvalue(),
    }}))
""")
    return _run_subprocess(runner, timeout + _SUBPROCESS_OVERHEAD, checks=["syntax", "import", "runtime"])


def run_bandit(files: list[str]) -> dict[str, Any]:
    """Security scan with bandit -ll (MEDIUM+ severity). Requires bandit installed.

    HIGH severity → blocking. MEDIUM → warning only.
    Returns: {status, summary, high_severity_count, medium_severity_count, findings, blocking}
    """
    py_files = [f for f in files if f.endswith(".py") and Path(f).exists()]
    if not py_files:
        return {"status": "skip", "summary": "no python files", "blocking": False,
                "high_severity_count": 0, "medium_severity_count": 0, "findings": []}
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            ["bandit", "-q", "-f", "json", "-ll", *py_files],
            capture_output=True, text=True, timeout=_STATIC_TIMEOUT,
        )
        dur = int((time.monotonic() - t0) * 1000)
        try:
            data = json.loads(result.stdout)
            results = data.get("results", [])
            high = [r for r in results if r.get("issue_severity") == "HIGH"]
            med = [r for r in results if r.get("issue_severity") == "MEDIUM"]
            return {
                "status": "fail" if high else ("warn" if med else "pass"),
                "summary": f"{len(high)} HIGH, {len(med)} MEDIUM findings",
                "high_severity_count": len(high),
                "medium_severity_count": len(med),
                "findings": results[:20],
                "blocking": bool(high),
                "duration_ms": dur,
            }
        except json.JSONDecodeError:
            return {"status": "skip", "summary": "bandit output not JSON", "blocking": False,
                    "high_severity_count": 0, "medium_severity_count": 0, "findings": []}
    except FileNotFoundError:
        return {"status": "skip", "summary": "bandit not installed (pip install bandit)",
                "blocking": False, "high_severity_count": 0, "medium_severity_count": 0, "findings": []}
    except subprocess.TimeoutExpired:
        return {"status": "skip", "summary": "bandit timeout", "blocking": False,
                "high_severity_count": 0, "medium_severity_count": 0, "findings": []}


def validate_patch(
    file_path: str,
    new_content: str,
    test_code: str = "",
    timeout: int = _SANDBOX_TIMEOUT_DEFAULT,
    run_security: bool = False,
) -> dict[str, Any]:
    """Orchestrate all sandbox layers and return a unified result.

    Args:
        file_path:    Target file path (for extension-based decisions).
        new_content:  The proposed new file content.
        test_code:    Optional test snippet to run against the code.
        timeout:      Sandbox timeout in seconds.
        run_security: If True, run bandit security scan (requires bandit).

    Returns:
        {ok, layers, error?, blocking_reason?, elapsed_ms}
    """
    t0 = time.monotonic()
    layers: list[dict] = []
    is_python = file_path.endswith((".py", ".pyw"))

    if not is_python:
        return {"ok": True, "layers": [], "elapsed_ms": 0, "note": "non-python file — no sandbox"}

    syn = syntax_check(new_content)
    layers.append({"layer": "syntax", **syn})
    if not syn["ok"]:
        return {
            "ok": False,
            "layers": layers,
            "blocking_reason": f"SyntaxError: {syn.get('error')}",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    imp = import_check(file_path, new_content, timeout)
    layers.append({"layer": "import", **imp})
    if not imp["ok"]:
        return {
            "ok": False,
            "layers": layers,
            "blocking_reason": f"ImportError: {imp.get('error')}",
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    if test_code:
        combined = new_content + "\n\n" + test_code
        snip = run_snippet(combined, timeout)
        layers.append({"layer": "test_snippet", **snip})
        if not snip["ok"]:
            return {
                "ok": False,
                "layers": layers,
                "blocking_reason": f"Test failed: {snip.get('error')}",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }

    if run_security:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as tf:
            tf.write(new_content)
            tmp_path = tf.name
        try:
            sec = run_bandit([tmp_path])
            layers.append({"layer": "security", **sec})
            if sec.get("blocking"):
                return {
                    "ok": False,
                    "layers": layers,
                    "blocking_reason": f"Security: {sec.get('summary')}",
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return {"ok": True, "layers": layers, "elapsed_ms": int((time.monotonic() - t0) * 1000)}


def format_for_llm(result: dict[str, Any], file_path: str = "") -> str:
    """Render validate_patch result as a readable string for LLM context."""
    if result.get("ok"):
        layers = result.get("layers", [])
        names = [l["layer"] for l in layers]
        ms = result.get("elapsed_ms", 0)
        return f"✅ Sandbox OK — {file_path or '?'} — layers: {', '.join(names)} ({ms}ms)"
    reason = result.get("blocking_reason", result.get("error", "unknown error"))
    layers_ok = [l["layer"] for l in result.get("layers", []) if l.get("ok")]
    return (
        f"❌ Sandbox FAILED — {file_path or '?'}\n"
        f"   Reason: {reason}\n"
        f"   Passed: {', '.join(layers_ok) or 'none'}"
    )


def _run_subprocess(script: str, timeout: int, checks: list[str]) -> dict[str, Any]:
    t0 = time.monotonic()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        raw = (result.stdout or "").strip()
        if raw:
            try:
                data = json.loads(raw)
                data["checks"] = checks
                data["elapsed_ms"] = elapsed
                return data
            except json.JSONDecodeError:
                pass
        return {
            "ok": False,
            "error_type": "SubprocessError",
            "error": result.stderr.strip()[:500] or "no output",
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
            "checks": checks,
            "elapsed_ms": elapsed,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_type": "TimeoutError",
            "error": f"sandbox timeout after {timeout}s",
            "checks": checks,
            "elapsed_ms": timeout * 1000,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "checks": checks,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }
