"""Example: Use the TDD loop directly without the full agent.

This shows how to use tdd_loop() as a standalone tool —
useful if you want to integrate just the TDD loop into your own system.

Prerequisites:
  export OPENROUTER_API_KEY="your-key-here"
  pip install daedalus-agent

Run:
  python examples/tdd_standalone/run.py
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, "../..")

from daedalus.tdd import tdd_loop


# The test we want to pass (written first — TDD style)
TEST_CODE = """
from target import is_palindrome

assert is_palindrome("racecar") == True
assert is_palindrome("hello") == False
assert is_palindrome("A man a plan a canal Panama".replace(" ", "").lower()) == True
assert is_palindrome("") == True
assert is_palindrome("a") == True
print("All palindrome tests passed!")
"""

# Broken initial implementation (the TDD loop will fix it)
BROKEN_CODE = """
def is_palindrome(s: str) -> bool:
    return s == s[::-1]  # This will fail for the Panama test
"""


async def main():
    print("🔄 Running TDD loop...\n")

    async def on_iteration(it):
        status = "✓ PASS" if it.passed else "✗ FAIL"
        print(f"  Iteration {it.number}: {status} — {it.summary[:100]}")

    result = await tdd_loop(
        file_path="target.py",
        test_code=TEST_CODE,
        initial_code=BROKEN_CODE,
        max_iterations=5,
        on_iteration=on_iteration,
    )

    print()
    print(result.format())

    if result.ok:
        print("\n📄 Final code:")
        print("─" * 40)
        print(result.final_code)
        print("─" * 40)
        Path("target.py").write_text(result.final_code, encoding="utf-8")
        print("✅ Written to target.py")


if __name__ == "__main__":
    asyncio.run(main())
