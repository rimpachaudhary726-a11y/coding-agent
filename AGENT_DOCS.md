# AGENT_DOCS.md

| Tool | Description |
|------|-------------|
| cli.py | Command-line interface for the repository, handling argument parsing and entry-point execution. |
| agent.py | Core agent implementation, managing the agent loop, memory, and tool dispatch. |
| tools.py | Definition of the built-in tool functions (read_file, write_file, run_bash, etc.) and utility helpers. |
| provider_pool.py | Provides a pool of tool providers, handling registration and lookup of custom tools. |
| firebase_tools.py | Utilities for Firebase integration, including auth and Firestore operations. |
| github_tools.py | Functions for interacting with GitHub APIs, such as creating PRs and listing issues. |
| task_memory.py | Manages task-specific memory storage and retrieval for agents across ticks. |
| meta_builder.py | Functions for constructing agent system configurations and generating system prompts. |
| director.py | Orchestrates the simulation flow, handling tick progression and agent updates. |
| custom_tool_registry.py | Registry for user-defined custom tools, supporting dynamic loading and integration. |

## cli.py
*Command‑line interface for the repository, handling argument parsing and entry‑point execution.*
```python
#!/usr/bin/env python3
"""
cli.py — the installable entry point.

Usage:
    python cli.py                  interactive chat loop (real conversation memory)
    python cli.py "fix the bug in auth.py"     one-shot task
    python cli.py --plan "refactor the auth module"   plan mode: pauses for your
                                                       approval before any write/edit/bash

    # Unix piping:
    echo "fix the bug in auth.py" | python cli.py
    cat bug_report.txt | python cli.py "summarize and fix"
    cat bug_report.txt | python cli.py         # piped content alone becomes the task
    cat bug_report.txt | python cli.py --plan  # piped task, plan mode on

Diff-before-apply: whenever the agent stages changes with stage_write_file /
stage_edit_file and then calls apply_pending_changes, you'll see the full
diff here and be asked to approve, reject, or leave feedback before anything
is written to disk.

Project memory: if an AGENT.md (or CLAUDE.md) file exists in the current
 directory, it is auto-loaded and given to the agent as project context.

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
                    pass

            if name in ("submit_plan", "apply_pending_changes"):
                # Rendered separately/interactively by the approval prompts;
                # skip here to avoid printing raw JSON twice.
                continue

            print(f"   🔧 {name}({args})")


def _ask_confirmation(command):
    """Real pause — asks you directly before a destructive command runs."""
    print(f"\n⚠️  About to run a potentially destructive command:")
    print(f"   {command}")
    answer = input("   Allow this? [y/N] ").strip().lower()
    return answer == "y"


def _ask_plan_approval(plan_text):
    """
    Shown when the agent calls submit_plan in plan mode.
    Returns (approved: bool, feedback: str).
    """
    print("\n📝 Proposed plan:")
    print("   " + "\n   ".join(plan_text.strip().splitlines()))
    answer = input("\n   Approve this plan? [y/N/feedback] ").strip()
    if answer.lower() == "y":
        return True, ""
    if answer.lower() in ("n", ""):
        return False, "Plan rejected, no specific feedback given — please reconsider your approach."
    # Anything else typed is treated as feedback for a revision
    return False, answer


def _ask_diff_approval(diff_text):
    """
    Shown when the agent calls apply_pending_changes. Prints the full staged
    diff across every file and asks for approval before anything is written.
    Returns (approved: bool, feedback: str).
    """
    print("\n📄 Pending changes (nothing written to disk yet):\n")
    print(diff_text)
    answer = input("\n   Apply these changes? [y/N/feedback] ").strip()
    if answer.lower() == "y":
        return True, ""
    if answer.lower() in ("n", ""):
        return False, "Changes rejected, no specific feedback given."
    return False, answer


def _read_stdin_if_piped():
    """Return piped stdin content, or None if stdin is a real terminal (no pipe)."""
    if sys.stdin.isatty():
        return None
    data = sys.stdin.read().strip()
    return data or None


def _parse_args(argv):
    """Extract --plan flag and remaining task words from CLI args."""
    plan_mode = False
    task_words = []
    for arg in argv:
        if arg == "--plan":
            plan_mode = True
        else:
            task_words.append(arg)
    return plan_mode, " ".join(task_words)


def main():
    plan_mode, cli_task = _parse_args(sys.argv[1:])
    piped_input = _read_stdin_if_piped()

    shared_kwargs = {"diff_confirm_callback": _ask_diff_approval}
    if plan_mode:
        shared_kwargs["plan_mode"] = True
        shared_kwargs["plan_confirm_callback"] = _ask_plan_approval

    # Case 1: CLI arg task, possibly combined with piped context
    if cli_task:
        task = cli_task
        if piped_input:
            task = f"{task}\n\n---\n{piped_input}"
        if plan_mode:
            print("=== Coding Agent CLI (plan mode) ===")
        result = run_agent(task, on_step=_print_step, confirm_callback=_ask_confirmation, **shared_kwargs)
        print(f"\n✅ {result}")
        return

    # Case 2: no CLI arg, but stdin was piped — piped content IS the task
    if piped_input:
        print(f"=== Coding Agent CLI (piped input{', plan mode' if plan_mode else ''}) ===")
        result = run_agent(piped_input, on_step=_print_step, confirm_callback=_ask_confirmation, **shared_kwargs)
        print(f"\n✅ {result}")
        return

    # Case 3: normal interactive mode
    print("=== Coding Agent CLI ===" + (" (plan mode)" if plan_mode else ""))
    print("Type your task, or 'quit' to exit.\n")

    conversation = Conversation(on_step=_print_step, confirm_callback=_ask_confirmation, **shared_kwargs)

    while True:
        try:
            task = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting interactive CLI.")
            break
        if task.lower() == "quit":
            break
        result = run_agent(task, on_step=_print_step, confirm_callback=_ask_confirmation, **shared_kwargs)
        print(f"\n✅ {result}")

if __name__ == "__main__":
    main()
```