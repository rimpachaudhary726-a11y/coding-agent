#!/usr/bin/env python3
"""
cli.py — the installable entry point.

Usage:
    python cli.py                              interactive chat loop (real conversation memory)
    python cli.py "fix the bug in auth.py"     one-shot task, human-readable step output

Scripting / Unix-pipeline mode:
    python cli.py -p "task"                    print mode — task + final result only, no
                                                emoji/step noise, safe to pipe or redirect
    python cli.py -p --json "task"             same, but result is a single JSON object on
                                                stdout — for jq / other tools to parse
    cat error.log | python cli.py -p "explain this"
                                                stdin is piped in and appended to the task as
                                                context automatically
    git diff | python cli.py -p --json "review this diff" > review.json

Exit codes (only meaningful in -p mode; interactive mode always exits 0 on quit):
    0   task completed without an ERROR result
    1   task result started with "ERROR" (tool failure, bad args, LLM error, etc)
    2   could not read stdin / bad CLI usage

Setup: set your API keys as environment variables (or Replit Secrets):
    CEREBRAS_API_KEY, CEREBRAS_API_KEY_2, ... CEREBRAS_API_KEY_9
    GROQ_API_KEY
    OPENROUTER_API_KEY, OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3
"""

import argparse
import json
import sys
import time
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

            print(f"   🔧 {name}({args})")


def _ask_confirmation(command):
    """Real pause — asks you directly before a destructive command runs."""
    print(f"\n⚠️  About to run a potentially destructive command:")
    print(f"   {command}")
    answer = input("   Allow this? [y/N] ").strip().lower()
    return answer == "y"


def _read_piped_stdin():
    """
    Returns piped stdin content as a string, or None if stdin is a real terminal
    (i.e. nothing was piped in). Never blocks waiting for interactive input.
    """
    if sys.stdin.isatty():
        return None
    try:
        data = sys.stdin.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"ERROR: could not read piped stdin: {e}", file=sys.stderr)
        sys.exit(2)
    return data if data.strip() else None


def _build_task_with_context(task, piped_context):
    if not piped_context:
        return task
    return (
        f"{task}\n\n"
        f"--- piped input (from stdin) ---\n"
        f"{piped_context}"
    )


def _run_print_mode(task, as_json, auto_confirm):
    """
    Non-interactive scripting mode: no step rendering, no confirmation prompts
    (destructive commands are auto-declined unless auto_confirm is set, since
    there's no human to ask), just the final result — either plain text or a
    single JSON object on stdout.
    """
    started = time.time()
    confirm_callback = None if auto_confirm else (lambda cmd: False)

    result = run_agent(task, on_step=None, auto_confirm=auto_confirm, confirm_callback=confirm_callback)
    elapsed = round(time.time() - started, 2)
    is_error = isinstance(result, str) and result.strip().upper().startswith("ERROR")

    if as_json:
        print(json.dumps({
            "task": task,
            "result": result,
            "elapsed_seconds": elapsed,
            "error": is_error,
        }))
    else:
        print(result)

    sys.exit(1 if is_error else 0)


def _run_one_shot_human(task):
    result = run_agent(task, on_step=_print_step, confirm_callback=_ask_confirmation)
    print(f"\n✅ {result}")
    is_error = isinstance(result, str) and result.strip().upper().startswith("ERROR")
    sys.exit(1 if is_error else 0)


def _run_interactive():
    print("=== Coding Agent CLI ===")
    print("Type your task, or 'quit' to exit.\n")

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
        result = conversation.send(task)
        print(f"\n✅ {result}")


def main():
    parser = argparse.ArgumentParser(
        description="Coding agent — interactive by default, scriptable with -p.",
        add_help=True,
    )
    parser.add_argument("task", nargs="*", help="the task to run (omit for interactive mode)")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true",
                         help="non-interactive scripting mode: no step noise, safe to pipe")
    parser.add_argument("--json", action="store_true",
                         help="with -p, emit a single JSON object on stdout instead of plain text")
    parser.add_argument("--auto-confirm", action="store_true",
                         help="with -p, auto-approve destructive commands instead of auto-declining "
                              "them (there's no human to ask in script mode — default is safe/decline)")
    args = parser.parse_args()

    piped_context = _read_piped_stdin()
    task_text = " ".join(args.task).strip()

    if args.json and not args.print_mode:
        print("ERROR: --json only applies with -p/--print.", file=sys.stderr)
        sys.exit(2)

    if args.print_mode:
        if not task_text and not piped_context:
            print("ERROR: -p/--print needs a task argument or piped stdin.", file=sys.stderr)
            sys.exit(2)
        final_task = _build_task_with_context(task_text or "Analyze the following input.", piped_context)
        _run_print_mode(final_task, as_json=args.json, auto_confirm=args.auto_confirm)
        return  # unreachable — _run_print_mode calls sys.exit()

    # Non-print modes below always go through human-readable rendering.
    if task_text or piped_context:
        final_task = _build_task_with_context(task_text or "Analyze the following input.", piped_context)
        _run_one_shot_human(final_task)
        return

    if not sys.stdin.isatty():
        # Piped stdin but no interactive terminal available and no -p given —
        # can't fall back to input() prompts, so treat it as one-shot instead
        # of hanging forever waiting for a TTY that isn't there.
        print("ERROR: input is piped but no task was given and stdin isn't a terminal. "
              "Use -p for scripting mode, e.g.: cat file | python cli.py -p \"task\"",
              file=sys.stderr)
        sys.exit(2)

    _run_interactive()


if __name__ == "__main__":
    main()
