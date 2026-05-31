"""CLI entry point — `python -m daedalus` or `daedalus` command."""
from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="daedalus",
        description="Daedalus-Agent — autonomous coding agent",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run agent with a prompt")
    run_p.add_argument("prompt", nargs="?", help="Task to run (or reads from stdin)")
    run_p.add_argument("--config", default="config.yml", help="Config file (default: config.yml)")
    run_p.add_argument("--model", help="Override model")

    tdd_p = sub.add_parser("tdd", help="Run TDD loop on a file")
    tdd_p.add_argument("file", help="Target file path")
    tdd_p.add_argument("test", help="Test file or inline test code")
    tdd_p.add_argument("--max-iter", type=int, default=5)

    lint_p = sub.add_parser("lint", help="Lint a Python file")
    lint_p.add_argument("file", help="File to lint")

    proposals_p = sub.add_parser("proposals", help="List pending proposals")
    proposals_p.add_argument("--approve", metavar="ID", help="Approve a proposal")
    proposals_p.add_argument("--reject", metavar="ID", help="Reject a proposal")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(_cmd_run(args))
    elif args.command == "tdd":
        asyncio.run(_cmd_tdd(args))
    elif args.command == "lint":
        _cmd_lint(args)
    elif args.command == "proposals":
        _cmd_proposals(args)
    else:
        parser.print_help()


async def _cmd_run(args) -> None:
    import os
    from pathlib import Path
    from .agent import Agent, AgentConfig

    prompt = args.prompt
    if not prompt:
        print("Enter task (Ctrl+D to finish):", file=sys.stderr)
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("No prompt provided.", file=sys.stderr)
        sys.exit(1)

    config_path = args.config
    if Path(config_path).exists():
        agent = Agent.from_config(config_path)
    else:
        cfg = AgentConfig.from_env()
        if args.model:
            cfg.model = args.model
        agent = Agent(cfg)

    print(f"🤖 Daedalus running: {prompt[:80]}...\n", file=sys.stderr)
    result = await agent.run(prompt)

    if result.ok:
        print(result.response)
        print(f"\n[{result.iterations} iterations, {result.elapsed_ms}ms, "
              f"{len(result.tool_calls)} tool calls]", file=sys.stderr)
    else:
        print(f"❌ Error: {result.error}", file=sys.stderr)
        sys.exit(1)


async def _cmd_tdd(args) -> None:
    from pathlib import Path
    from .tdd import tdd_loop

    test_path = Path(args.test)
    if test_path.exists():
        test_code = test_path.read_text("utf-8")
    else:
        test_code = args.test

    print(f"🔄 TDD loop: {args.file} (max {args.max_iter} iterations)\n", file=sys.stderr)

    async def on_iter(it):
        status = "✓" if it.passed else "✗"
        print(f"  [{status}] iter {it.number}: {it.summary[:100]}", file=sys.stderr)

    result = await tdd_loop(
        file_path=args.file,
        test_code=test_code,
        max_iterations=args.max_iter,
        on_iteration=on_iter,
    )
    print(result.format())
    if result.ok:
        resp = input(f"\nWrite fixed code to {args.file}? [y/N] ")
        if resp.strip().lower() == "y":
            Path(args.file).write_text(result.final_code, encoding="utf-8")
            print("✅ Written.")
    else:
        sys.exit(1)


def _cmd_lint(args) -> None:
    from .lint import lint_file, format_result
    result = lint_file(args.file)
    print(format_result(result))
    if not result.ok:
        sys.exit(1)


def _cmd_proposals(args) -> None:
    from .proposals import ProposalStore
    store = ProposalStore()

    if args.approve:
        r = store.approve(args.approve)
        print("✅ Approved" if r["ok"] else f"❌ {r['error']}")
        return
    if args.reject:
        r = store.reject(args.reject)
        print("✅ Rejected" if r["ok"] else f"❌ {r['error']}")
        return

    pending = store.list_pending()
    if not pending:
        print("No pending proposals.")
        return
    for p in pending:
        print(f"\n[{p.id}] {p.file_path}")
        print(f"  By: {p.proposed_by} | Reason: {p.reason[:80]}")
        print(f"  Lint warnings: {len(p.lint_warnings)} | Status: {p.status}")
    print(f"\nTotal: {len(pending)} pending proposals")
    print("Approve: daedalus proposals --approve <id>")
    print("Reject:  daedalus proposals --reject <id>")


if __name__ == "__main__":
    main()
