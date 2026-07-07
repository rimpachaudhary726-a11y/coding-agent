#!/usr/bin/env python3
"""
cli.py — the installable entry point.

Usage:
    python cli.py                  interactive chat loop (real conversation memory)
    python cli.py "fix the bug in auth.py"     one-shot task (no follow-up context needed)

Setup: set your API keys as environment variables (or Replit Secrets):
    CEREBRAS_API_KEY, CEREBRAS_API_KEY_2, ... CEREBRAS_API_KEY_9
    GROQ_API_KEY
    OPENROUTER_API_KEY, OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3
"""

import sys
import json
from agent import run_agent, Conversation


def _print_step(step):
    if step["content"]:
        print(f"\n🤖 {step['content']}")
    if step["tool_calls"]:
        for tc in step["tool_calls"]:
            fn = tc.get("function") if isinstance(tc, dict) else tc.function
            name = fn.get("name") if isinstance(fn, dict) else fn.name
            args = fn.get("arguments") if isinstance(fn, dict) else fn.arguments

            if name == "write_todos":
                try:
                    todos = json.loads(args).get("todos", [])
                    print("\n📋 Plan:")
                    marker = {"pending": "☐", "in_progress": "◐", "completed": "☑"}
                    for item in todos:
                        print(f"   {marker.get(item.get('status'), '☐')} {item.get('content', '')}")
                    continue
                except (json.JSONDecodeError, AttributeError):
                    pass  # fall through to generic rendering below

            print(f"   🔧 {name}({args})")


def _ask_confirmation(command):
    """Real pause — asks you directly before a destructive command runs."""
    print(f"\n⚠️  About to run a potentially destructive command:")
    print(f"   {command}")
    answer = input("   Allow this? [y/N] ").strip().lower()
    return answer == "y"


def main():
    print("=== Coding Agent CLI ===")
    print("Type your task, or 'quit' to exit.")
    print("Prefix a task with 'plan: ' to investigate and propose a plan first, without changing anything.\n")

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        result = run_agent(task, on_step=_print_step, confirm_callback=_ask_confirmation)
        print(f"\n✅ {result}")
        return

    conversation = Conversation(on_step=_print_step, confirm_callback=_ask_confirmation)

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
        if task.lower() == "new":
            conversation = Conversation(on_step=_print_step, confirm_callback=_ask_confirmation)
            print("(started a fresh conversation)")
            continue

        if task.lower().startswith("plan:"):
            real_task = task[len("plan:"):].strip()
            print("\n🔒 Plan mode — investigating only, nothing will be changed yet.")
            plan_result = run_agent(
                real_task, on_step=_print_step, use_memory=False, plan_mode=True
            )
            print(f"\n📋 {plan_result}")
            approve = input("\nExecute this now with full tools? [y/N] ").strip().lower()
            if approve == "y":
                result = conversation.send(real_task)
                print(f"\n✅ {result}")
            else:
                print("(not executed)")
            continue

        result = conversation.send(task)
        print(f"\n✅ {result}")


if __name__ == "__main__":
    main()
