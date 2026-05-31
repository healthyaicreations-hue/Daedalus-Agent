"""Simple example: Daedalus writes and tests a Python function autonomously.

Prerequisites:
  export OPENROUTER_API_KEY="your-key-here"
  pip install daedalus-agent

Run:
  python examples/simple_coding_agent/run.py
"""
import asyncio
import sys
sys.path.insert(0, "../..")

from daedalus import Agent
from daedalus.agent import AgentConfig


async def main():
    # Configure the agent
    config = AgentConfig.from_env()
    config.model = "deepseek/deepseek-v3.2"
    agent = Agent(config)

    # Give it a task — it will use NEED_TDD to write and test the code autonomously
    task = """
    Write a Python function `parse_csv_line(line: str) -> list[str]` that:
    - Splits a CSV line by comma
    - Handles quoted fields (fields wrapped in double quotes can contain commas)
    - Strips leading/trailing whitespace from unquoted fields
    - Returns a list of strings

    Use NEED_TDD to write the function in `output/csv_parser.py` with a proper test.
    Then show me the final result.
    """

    print("🤖 Starting Daedalus agent...\n")
    result = await agent.run(task)

    if result.ok:
        print("━" * 60)
        print(result.response)
        print("━" * 60)
        print(f"\n✅ Done in {result.iterations} iterations, {result.elapsed_ms}ms")
        print(f"   Tool calls: {[c['tool'] for c in result.tool_calls]}")
    else:
        print(f"❌ Error: {result.error}")


if __name__ == "__main__":
    asyncio.run(main())
