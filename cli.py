#!/usr/bin/env python3
"""
cli.py — the installable entry point.

Usage:
    python cli.py                  interactive chat loop
    python cli.py "fix the bug in auth.py"     one-shot task

Setup: set your API keys as environment variables (or Replit Secrets):
    CEREBRAS_API_KEY, CEREBRAS_API_KEY_2, ... CEREBRAS_API_KEY_9
    GROQ_API_KEY
    OPENROUTER_API_KEY, OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3
"""

import sys
from agent import run_agent


def _print_step(step):
    if step["content"]:
        print(f"\n🤖 {step['content']}")
    if step["tool_calls"]:
        for tc in step["tool_calls"]:
            fn = tc.get("function") if isinstance(tc, dict) else tc.function
            name = fn.get("name") if isinstance(fn, dict) else fn.name
            args = fn.get("arguments") if isinstance(fn, dict) else fn.arguments
            print(f"   🔧 {name}({args})")


def main():
    print("=== Coding Agent CLI ===")
    print("Type your task, or 'quit' to exit.\n")

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        result = run_agent(task, on_step=_print_step)
        print(f"\n✅ {result}")
        return

    while True:
        try:
            task = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not task:
            continue
        if task.lower() in ("quit", "exit"):
            print("bye")
            break
        result = run_agent(task, on_step=_print_step)
        print(f"\n✅ {result}")


if __name__ == "__main__":
    main()
