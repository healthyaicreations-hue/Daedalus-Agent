"""Daedalus-Agent — Autonomous coding agent framework.

Core modules:
  sandbox   — isolated Python execution (5 layers)
  lint      — proactive ruff lint gate (F/E/W)
  workers   — parallel LLM worker slots (fast/balanced/deep/coder/reviewer)
  tdd       — autonomous TDD loop (test → fix → iterate → proposal)
  storage   — pluggable storage (JSON file, SQLite, or custom)
  agent     — tool loop orchestrator

Quick start:
  from daedalus import Agent
  agent = Agent.from_config("config.yml")
  await agent.run("Implement a fibonacci function with tests")
"""
from .agent import Agent
from .sandbox import syntax_check, run_snippet, validate_patch
from .lint import lint_content, lint_file, LintResult
from .tdd import tdd_loop, TDDResult
from .workers import call_workers_parallel, WorkerConfig

__all__ = [
    "Agent",
    "syntax_check", "run_snippet", "validate_patch",
    "lint_content", "lint_file", "LintResult",
    "tdd_loop", "TDDResult",
    "call_workers_parallel", "WorkerConfig",
]

__version__ = "0.1.0"
