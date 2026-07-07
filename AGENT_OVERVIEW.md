# Coding Agent — Complete Overview

> **Last synced:** All 19 Python files read from latest source.

A self-directing coding agent that reads, writes, edits, and runs code via LLM tool-calling. It rotates across multiple API providers, maintains importance-weighted persistent memory, supports parallel fan-out and sequential sub-agent delegation, has a first-class plan-gating system, a diff-before-apply staged changes workflow, headless browser control with visual self-verification, AST-aware code search, a full LSP client, structured tool-call logging, and can create its own new tools at runtime. It also ships a complete N-agent simulation engine.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [cli.py — Entry Point](#clipy--entry-point)
3. [agent.py — Core Loop](#agentpy--core-loop)
4. [tools.py — Built-in Tools](#toolspy--built-in-tools)
5. [vision_tools.py — Browser & Visual Verification](#vision_toolspy--browser--visual-verification)
6. [provider_pool.py — LLM Rotation](#provider_poolpy--llm-rotation)
7. [task_memory.py — Cross-Session Memory](#task_memorypy--cross-session-memory)
8. [custom_tool_registry.py — Self-Created Tools](#custom_tool_registrypy--self-created-tools)
9. [custom_tools.py — Agent-Created Tools](#custom_toolspy--agent-created-tools)
10. [run_logger.py — Tool Call Logger](#run_loggerpy--tool-call-logger)
11. [structural_search.py — AST Code Search](#structural_searchpy--ast-code-search)
12. [lsp_client.py — Language Server Client](#lsp_clientpy--language-server-client)
13. [file_search.py — Glob File Search](#file_searchpy--glob-file-search)
14. [firebase_tools.py — Firebase Scaffolding](#firebase_toolspy--firebase-scaffolding)
15. [github_tools.py — GitHub Integration](#github_toolspy--github-integration)
16. [meta_builder.py — Simulation Generator](#meta_builderpy--simulation-generator)
17. [tick_engine.py — Simulation Runtime](#tick_enginepy--simulation-runtime)
18. [memory.py — Per-Agent Memory Store](#memorypy--per-agent-memory-store)
19. [director.py — Event Injection](#directorpy--event-injection)
20. [Utility Files](#utility-files)
21. [Environment Variables](#environment-variables)
22. [Full System Architecture](#full-system-architecture)

---

## Project Structure

```
.
├── cli.py                    # Entry point — interactive, one-shot, --plan flag, stdin piping
├── agent.py                  # Core loop: plan gating, sub-agent delegation, AGENT.md loading
├── tools.py                  # Built-in tools — immediate writes + staged diff-before-apply
├── vision_tools.py           # Browser control, screenshot cropping, visual self-verification
├── provider_pool.py          # Multi-key LLM rotation with Retry-After-aware cooldowns
├── task_memory.py            # Importance-weighted cross-session semantic memory
├── custom_tool_registry.py   # Agent self-creates tools — timeout-guarded validation + runtime
├── custom_tools.py           # Auto-generated — tools the agent has built itself
├── run_logger.py             # Structured JSONL logging of every tool call + latency
├── structural_search.py      # AST-aware Python code search (definition/callers/references/outline)
├── lsp_client.py             # Real LSP client — diagnostics, hover, go-to-definition via pylsp
├── file_search.py            # Glob-style file discovery by name/path pattern
├── firebase_tools.py         # Generates Firebase auth + Firestore boilerplate
├── github_tools.py           # Git push, branch, PR, issue listing via GitHub REST API
├── meta_builder.py           # Generates a full N-agent simulation from one prompt
├── tick_engine.py            # Simulation tick loop (one hour per tick)
├── memory.py                 # Per-agent JSON memory store for the simulation
├── director.py               # Inject world/personal events into a running simulation
├── calc.py                   # Utility: safe division (agent-written example)
├── greeter.py                # Utility: greeting helper (agent-written example)
├── mathutils.py              # Utility: math helpers (agent-written example)
├── shapes.py                 # Utility: Circle and Square classes (agent-written example)
├── test_calc.py              # Unit tests for calc.py
└── requirements.txt          # Python dependencies (requests)
```

---

## cli.py — Entry Point

### Features

| Feature | Description |
|---|---|
| **Interactive mode** | Growing `Conversation` history across turns |
| **One-shot mode** | `python cli.py "task"` — fresh context, exits |
| **`--plan` flag** | Restricts agent to read-only tools until plan approved; applies to all three modes |
| **stdin piping** | `echo "task" \| python cli.py`, `cat file.txt \| python cli.py "summarize and fix"` |
| **Live step rendering** | Every tool call and LLM reply streamed to terminal |
| **Todo display** | `write_todos` rendered as ☐ ◐ ☑ checklist |
| **Destructive confirmation** | `_ask_confirmation()` pauses before dangerous shell commands |
| **Plan approval UI** | `_ask_plan_approval()` — shows proposed plan, accepts `y` / `N` / feedback text |
| **Diff approval UI** | `_ask_diff_approval()` — shows full staged diff before any writes, accepts `y` / `N` / feedback |
| **`new` command** | Reset conversation without restarting |
| **Clean exit** | Handles `EOFError` and `KeyboardInterrupt` |

### Usage

```bash
python cli.py                                    # interactive
python cli.py "fix the bug in auth.py"           # one-shot
python cli.py --plan "refactor the auth module"  # plan mode one-shot

echo "fix the import error" | python cli.py          # piped task
cat bug_report.txt | python cli.py "summarize and fix"  # piped context + task
cat bug_report.txt | python cli.py --plan             # piped task in plan mode
```

### Plan Mode (`--plan`)

When `--plan` is passed, the agent is restricted to read-only tools until it calls `submit_plan`. The CLI intercepts `submit_plan`, prints the plan, and prompts for approval:
- `y` → plan approved, agent proceeds with full tool set
- `N` or empty → plan rejected, agent revises and re-submits
- Any other text → treated as feedback, sent back to the agent

### Diff Review (`apply_pending_changes`)

When the agent stages changes with `stage_write_file` / `stage_edit_file` and calls `apply_pending_changes`, the CLI intercepts it, prints the full unified diff across all staged files, and prompts for approval before anything hits disk.

### Full Code

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
            print("\nbye")
            break
        if not task:
            continue
        if task.lower() in ("quit", "exit"):
            print("bye")
            break
        if task.lower() == "new":
            conversation = Conversation(on_step=_print_step, confirm_callback=_ask_confirmation, **shared_kwargs)
            print("(started a fresh conversation)")
            continue
        result = conversation.send(task)
        print(f"\n✅ {result}")


if __name__ == "__main__":
    main()
```

---

## agent.py — Core Loop

### Features

| Feature | Description |
|---|---|
| **Tool-calling loop** | Up to 40 LLM ↔ tool turns per task |
| **`run_agent()`** | Single-shot runner — fresh context per call |
| **`Conversation`** | Stateful multi-turn session with growing history |
| **Plan mode** | `plan_mode=True` restricts to read-only tools until `submit_plan` approved |
| **`submit_plan` tool** | Agent proposes plan; `plan_confirm_callback` gates approval; defense-in-depth blocks write tools until approved |
| **Diff mode** | `diff_confirm_callback` gates `apply_pending_changes` — human sees full diff before any write |
| **`delegate_subagent()`** | Fresh isolated sub-conversation; only returns final summary; no recursion; no destructive commands |
| **`AGENT.md` auto-load** | Reads `AGENT.md`, `CLAUDE.md`, or `.agent/AGENT.md` from project root, injects into system prompt |
| **Task memory** | Injects relevant past summaries; saves on completion |
| **Custom tool hot-reload** | After `create_tool`, new tool available next turn |
| **Fan-out** | Parallel `run_agent()` via `ThreadPoolExecutor` |
| **Empty-response guard** | Nudges model on blank replies; errors after 2 in a row |

### Plan Mode Mechanics

1. When `plan_mode=True`, only tools in `READ_ONLY_TOOL_NAMES` are exposed to the model.
2. Agent investigates using read-only tools, then calls `submit_plan`.
3. `plan_confirm_callback(plan_text)` returns `(approved: bool, feedback: str)`.
4. If approved: full tool set unlocked, `plan_approved = True` carried through remaining turns.
5. If rejected: feedback sent back, agent revises and re-submits.
6. Defense-in-depth: even if the model emits a write tool call before approval, `_run_loop` blocks it explicitly.

### Read-Only Tools (allowed before plan approval)

```
read_file  read_files  list_directory  search_codebase  write_todos
submit_plan  list_open_issues
browser_navigate  browser_screenshot  screen_crop  visual_self_verify
review_pending_changes
```

### AGENT.md Auto-Loading

On every `run_agent()` call and `Conversation()` init, `_load_project_context()` searches the project root for `AGENT.md` → `CLAUDE.md` → `.agent/AGENT.md` (first found wins). Content is truncated to 8 000 chars and prepended to the system prompt as `"Project context from AGENT.md:\n..."`. This lets teams encode project-specific conventions that the agent always sees.

### delegate_subagent

Spins up a fresh, isolated `_run_loop` with a clean context. The sub-agent:
- Gets its own system prompt with an addendum explaining it's a sub-agent
- Cannot call `delegate_subagent` (recursion prevention)
- Cannot run destructive bash commands (no `confirm_callback` wired through)
- Returns only its final plain-text summary — the full tool-call history stays out of the parent's context
- Default max 15 turns

### Full Code

```python
"""
agent.py — the core reasoning loop, plus fan-out for big tasks.

Features in this version:
  - AGENT.md project memory: auto-loaded from the project root (if present)
    and injected into the system prompt.
  - Plan mode: agent restricted to read-only tools until submit_plan is
    approved by the human.
  - Diff-before-apply: apply_pending_changes (from tools.py) is gated behind
    a human reviewing the full staged diff, the same way destructive bash
    commands are gated.
  - Sub-agent delegation: delegate_subagent spins up a fresh, isolated
    conversation for a self-contained chunk of work and returns just its
    final summary — sequential and synchronous, unlike fan_out's parallel
    multi-target execution.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from provider_pool import ask_ai
from tools import (
    TOOL_SCHEMA, TOOL_FUNCTIONS, _matches_any, _DESTRUCTIVE_PATTERNS,
    review_pending_changes,
)
from firebase_tools import FIREBASE_TOOL_SCHEMA, FIREBASE_TOOL_FUNCTIONS
from github_tools import GITHUB_TOOL_SCHEMA, GITHUB_TOOL_FUNCTIONS
from vision_tools import VISION_TOOL_SCHEMA, VISION_TOOL_FUNCTIONS
from meta_builder import build_agent_system
from custom_tool_registry import CREATE_TOOL_SCHEMA, CREATE_TOOL_FUNCTIONS, load_custom_tools
import task_memory

META_BUILDER_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "build_agent_system",
        "description": "Generate a complete N-agent simulation system (personas with personality/"
                        "routine/relationships/secrets, memory storage, tick loop, and a live web "
                        "viewer) for a given theme.",
        "parameters": {"type": "object", "properties": {
            "theme": {"type": "string"},
            "count": {"type": "integer", "default": 30},
            "output_dir": {"type": "string", "default": "."},
        }, "required": ["theme"]},
    }},
]
META_BUILDER_TOOL_FUNCTIONS = {"build_agent_system": build_agent_system}


# ---------------------------------------------------------------------------
# Plan mode — submit_plan tool. Actual gating happens in _run_loop.
# ---------------------------------------------------------------------------

def submit_plan(plan):
    """Placeholder body — _run_loop intercepts this call before it ever runs."""
    return plan


PLAN_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "submit_plan",
        "description": "Submit your implementation plan for human approval. In plan mode, you "
                        "MUST call this before using any file-writing, editing, or shell tool. "
                        "Describe the concrete steps you intend to take.",
        "parameters": {"type": "object", "properties": {
            "plan": {"type": "string", "description": "The step-by-step plan, in plain text."},
        }, "required": ["plan"]},
    }},
]
PLAN_TOOL_FUNCTIONS = {"submit_plan": submit_plan}

# Tools allowed before a plan has been approved (read-only / planning only)
READ_ONLY_TOOL_NAMES = {
    "read_file", "read_files", "list_directory", "search_codebase",
    "write_todos", "submit_plan", "list_open_issues",
    "browser_navigate", "browser_screenshot", "screen_crop", "visual_self_verify",
    "review_pending_changes",
}


# ---------------------------------------------------------------------------
# Sub-agent delegation
# ---------------------------------------------------------------------------

SUBAGENT_SYSTEM_ADDENDUM = """

You are a SUB-AGENT handling one isolated, self-contained piece of work \
delegated by a parent agent. You do not have access to delegate_subagent \
yourself, to avoid unbounded recursion. Focus only on the task given. \
When finished, reply with a concise plain-text summary of what you found \
or did — this summary is the ONLY thing the parent agent will see, so make \
it complete enough to act on."""

# Tools excluded from a sub-agent's own tool set (prevents infinite recursion)
SUBAGENT_EXCLUDED_TOOLS = {"delegate_subagent"}


def delegate_subagent(task, max_turns=15):
    """
    Run an isolated, fresh-context sub-conversation for a self-contained task.
    Returns only the sub-agent's final plain-text summary.
    """
    sub_schema = [e for e in TOOL_SCHEMA if e["function"]["name"] not in SUBAGENT_EXCLUDED_TOOLS]
    sub_messages = [
        {"role": "system", "content": SYSTEM_PROMPT + SUBAGENT_SYSTEM_ADDENDUM},
        {"role": "user", "content": task},
    ]
    final, _ = _run_loop(
        sub_messages, on_step=None, auto_confirm=False, confirm_callback=None,
        plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None,
        tool_schema_override=sub_schema, max_turns=max_turns,
    )
    return final


DELEGATE_SUBAGENT_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "delegate_subagent",
        "description": "Delegate an isolated, self-contained chunk of work to a fresh sub-agent "
                        "with its own clean context (e.g. investigating a bug's root cause, "
                        "researching how something is used across the codebase, reviewing a "
                        "single file in depth). You only receive its final summary, not its "
                        "full tool-call history — use this to keep your own context lean on "
                        "large tasks. The sub-agent cannot run destructive commands or delegate "
                        "further.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "The self-contained task to delegate."},
            "max_turns": {"type": "integer", "default": 15},
        }, "required": ["task"]},
    }},
]
DELEGATE_SUBAGENT_TOOL_FUNCTIONS = {"delegate_subagent": delegate_subagent}


_custom_schema, _custom_functions, _custom_load_errors = load_custom_tools()
for _err in _custom_load_errors:
    print(f"[custom tools] {_err}")

TOOL_SCHEMA = (
    TOOL_SCHEMA + FIREBASE_TOOL_SCHEMA + GITHUB_TOOL_SCHEMA + VISION_TOOL_SCHEMA
    + META_BUILDER_TOOL_SCHEMA + CREATE_TOOL_SCHEMA + PLAN_TOOL_SCHEMA
    + DELEGATE_SUBAGENT_TOOL_SCHEMA + _custom_schema
)
TOOL_FUNCTIONS = {
    **TOOL_FUNCTIONS, **FIREBASE_TOOL_FUNCTIONS, **GITHUB_TOOL_FUNCTIONS, **VISION_TOOL_FUNCTIONS,
    **META_BUILDER_TOOL_FUNCTIONS, **CREATE_TOOL_FUNCTIONS, **PLAN_TOOL_FUNCTIONS,
    **DELEGATE_SUBAGENT_TOOL_FUNCTIONS, **_custom_functions,
}


def _refresh_custom_tools():
    """Hot-reload: makes a newly created tool available in the current session."""
    schema_list, functions, errors = load_custom_tools()
    existing_names = {entry["function"]["name"] for entry in TOOL_SCHEMA}
    for entry in schema_list:
        name = entry["function"]["name"]
        if name not in existing_names:
            TOOL_SCHEMA.append(entry)
    TOOL_FUNCTIONS.update(functions)
    return errors


# ---------------------------------------------------------------------------
# AGENT.md — project-level memory, auto-loaded once per run/conversation.
# ---------------------------------------------------------------------------

AGENT_MD_FILENAMES = ["AGENT.md", "CLAUDE.md", ".agent/AGENT.md"]
MAX_AGENT_MD_CHARS = 8000


def _load_project_context(root="."):
    """
    Look for a project memory file (AGENT.md, falling back to CLAUDE.md)
    in the project root and return its content, truncated to a sane size.
    Returns "" if none exists.
    """
    for filename in AGENT_MD_FILENAMES:
        path = os.path.join(root, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except OSError:
                continue
            if not content:
                continue
            if len(content) > MAX_AGENT_MD_CHARS:
                content = content[:MAX_AGENT_MD_CHARS] + "\n...(truncated)"
            return f"Project context from {filename}:\n{content}"
    return ""


SYSTEM_PROMPT = """You are a coding agent with direct access to the filesystem \
and shell via tools. You can read, write, and edit files, search the codebase, \
run commands, and commit to git. Work step by step: investigate before you \
change anything, make the smallest correct change, and verify your work \
(run tests or the relevant command) before declaring the task done. \
When a task is genuinely finished, reply with plain text and no further tool calls.

When looking for a file, check the project root first (list_directory(".")) \
before searching subfolders. Ignore artifacts/, node_modules/, lib/, .cache/, \
and other tooling/dependency folders unless the user's request specifically \
points there.

If the project has a test suite, call detect_and_run_tests after making code \
changes, before declaring the task done.

For a quick, single-file fix, write_file/edit_file (immediate, no review) are \
fine. For anything touching multiple files, or any change you want reviewed \
as a whole before it hits disk, use stage_write_file / stage_edit_file to \
queue the changes, call review_pending_changes to produce the full diff, then \
apply_pending_changes to commit everything at once (this requires human \
approval and will not silently write anything).

If a task genuinely needs a capability none of your existing tools provide, \
use create_tool to write and permanently save a new one. Prefer existing tools \
whenever they can do the job.

If you're stuck on a bug after a few real attempts, consider whether a diagnostic \
tool would help rather than continuing to guess blindly.

For any task with 3 or more distinct steps, call write_todos first to lay \
out the plan, then update it as steps complete.

If an edit makes things worse, use revert_file to get back to the last \
known-good state before trying a different approach.

For a self-contained chunk of investigation or work that doesn't need to \
pollute your own context with its full tool-call history (e.g. researching \
how something is used across the codebase, or diagnosing one specific bug), \
consider delegate_subagent and just use its returned summary.

For frontend/UI work, use the browser tools (browser_navigate, browser_click, \
browser_type, browser_screenshot) to actually see and interact with what you \
build, the way a human tester would. If a screenshot is too blurry, cluttered, \
or small to read a specific button or error message, use screen_crop to zoom \
into that region before deciding what to do next. After building or editing a \
frontend, take a screenshot and use visual_self_verify against the original \
design mockup (if one was provided) to confirm colors, spacing, and layout \
actually match before declaring the task done."""

PLAN_MODE_ADDENDUM = """

PLAN MODE IS ACTIVE. Before making any file edit, file write, or running any \
shell command, you must first investigate using read-only tools \
(read_file, list_directory, search_codebase, browser tools), then call \
submit_plan with a concrete step-by-step plan. Do not call any writing/editing/ \
shell tool until submit_plan has been approved. If the plan is rejected, revise \
it based on the feedback and call submit_plan again."""

MAX_TURNS = 40


def _execute_tool_call(tool_call, confirm_destructive=False, confirm_callback=None,
                        diff_confirm_callback=None):
    name = tool_call["function"]["name"]
    try:
        args = json.loads(tool_call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return f"ERROR: could not parse arguments for {name}"

    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f"ERROR: unknown tool '{name}'"

    if name == "run_bash":
        command = args.get("command", "")
        if confirm_destructive:
            args["confirmed"] = True
        elif confirm_callback and _matches_any(command.strip(), _DESTRUCTIVE_PATTERNS):
            allowed = confirm_callback(command)
            if allowed:
                args["confirmed"] = True
            else:
                return f"Command declined by user: '{command}'. Not run."

    if name == "apply_pending_changes" and not args.get("confirmed"):
        if confirm_destructive:
            args["confirmed"] = True
        elif diff_confirm_callback:
            diff_text = review_pending_changes()
            approved, feedback = diff_confirm_callback(diff_text)
            if approved:
                args["confirmed"] = True
            else:
                return (
                    "Changes NOT applied — user rejected the diff."
                    + (f" Feedback: {feedback}" if feedback else "")
                    + " Pending changes remain staged; revise with stage_write_file/"
                      "stage_edit_file and try again, or discard_pending_changes."
                )

    try:
        result = func(**args)
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:
        return f"ERROR: {name} raised an exception: {e}"

    if name == "create_tool" and isinstance(result, str) and result.startswith("Tool '"):
        refresh_errors = _refresh_custom_tools()
        if refresh_errors:
            result += f"\n(Note: {'; '.join(refresh_errors)})"

    return result


def run_agent(task, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True,
              plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None):
    memory_context = ""
    if use_memory:
        relevant = task_memory.retrieve_relevant(task)
        memory_context = task_memory.format_for_prompt(relevant)

    project_context = _load_project_context()

    system_content = SYSTEM_PROMPT
    if plan_mode:
        system_content += PLAN_MODE_ADDENDUM
    if project_context:
        system_content += "\n\n" + project_context
    if memory_context:
        system_content += "\n\n" + memory_context

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": task},
    ]

    final, _ = _run_loop(messages, on_step, auto_confirm, confirm_callback,
                          plan_mode=plan_mode, plan_confirm_callback=plan_confirm_callback,
                          diff_confirm_callback=diff_confirm_callback)
    if use_memory:
        task_memory.add_task_summary(task, final)
    return final


class Conversation:
    def __init__(self, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True,
                 plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None):
        self.on_step = on_step
        self.auto_confirm = auto_confirm
        self.confirm_callback = confirm_callback
        self.use_memory = use_memory
        self.plan_mode = plan_mode
        self.plan_confirm_callback = plan_confirm_callback
        self.diff_confirm_callback = diff_confirm_callback

        system_content = SYSTEM_PROMPT
        if plan_mode:
            system_content += PLAN_MODE_ADDENDUM
        project_context = _load_project_context()
        if project_context:
            system_content += "\n\n" + project_context

        self.messages = [{"role": "system", "content": system_content}]
        self._memory_applied = False
        self._first_task = None
        self._plan_approved = not plan_mode

    def send(self, task):
        if not self._memory_applied and self.use_memory:
            relevant = task_memory.retrieve_relevant(task)
            memory_context = task_memory.format_for_prompt(relevant)
            if memory_context:
                self.messages[0]["content"] += "\n\n" + memory_context
            self._memory_applied = True
            self._first_task = task

        self.messages.append({"role": "user", "content": task})
        final, self._plan_approved = _run_loop(
            self.messages, self.on_step, self.auto_confirm, self.confirm_callback,
            plan_mode=self.plan_mode, plan_confirm_callback=self.plan_confirm_callback,
            diff_confirm_callback=self.diff_confirm_callback,
            plan_already_approved=self._plan_approved,
        )
        self.messages.append({"role": "assistant", "content": final})

        if self.use_memory:
            task_memory.add_task_summary(self._first_task or task, final)

        return final


def _filtered_schema_for_plan_state(base_schema, plan_approved):
    """When a plan hasn't been approved yet, only expose read-only tools."""
    if plan_approved:
        return base_schema
    return [entry for entry in base_schema if entry["function"]["name"] in READ_ONLY_TOOL_NAMES]


def _run_loop(messages, on_step, auto_confirm, confirm_callback,
              plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None,
              plan_already_approved=False, tool_schema_override=None, max_turns=None):
    base_schema = tool_schema_override if tool_schema_override is not None else TOOL_SCHEMA
    turn_limit = max_turns if max_turns is not None else MAX_TURNS
    consecutive_empty = 0
    plan_approved = plan_already_approved or not plan_mode

    for turn in range(turn_limit):
        active_schema = _filtered_schema_for_plan_state(base_schema, plan_approved)
        message = ask_ai(messages, tools=active_schema)

        if isinstance(message, dict) and "error" in message:
            return f"ERROR: {message['error']}", plan_approved

        content = message.get("content") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else message.tool_calls

        if on_step:
            on_step({"turn": turn, "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            if not content or not content.strip():
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    return "ERROR: model returned empty responses repeatedly.", plan_approved
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": "Your last response was empty. Please continue."})
                continue
            return content, plan_approved

        consecutive_empty = 0
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            tc_dict = tc if isinstance(tc, dict) else {
                "id": tc.id,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            fn_name = tc_dict["function"]["name"]

            if fn_name == "submit_plan" and plan_mode and not plan_approved:
                try:
                    plan_args = json.loads(tc_dict["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    plan_args = {}
                plan_text = plan_args.get("plan", "")

                if plan_confirm_callback:
                    approved, feedback = plan_confirm_callback(plan_text)
                else:
                    approved, feedback = True, ""

                if approved:
                    plan_approved = True
                    result = "Plan approved by the user. You may now proceed using any tool."
                else:
                    result = (
                        "Plan REJECTED by the user."
                        + (f" Feedback: {feedback}" if feedback else "")
                        + " Revise your plan and call submit_plan again."
                    )
                messages.append({"role": "tool", "tool_call_id": tc_dict["id"], "content": result})
                continue

            # Defense in depth: block write tools if plan not yet approved
            if plan_mode and not plan_approved and fn_name not in READ_ONLY_TOOL_NAMES:
                messages.append({
                    "role": "tool", "tool_call_id": tc_dict["id"],
                    "content": f"BLOCKED: '{fn_name}' is not allowed until your plan is "
                               f"submitted via submit_plan and approved by the user.",
                })
                continue

            result = _execute_tool_call(
                tc_dict, confirm_destructive=auto_confirm, confirm_callback=confirm_callback,
                diff_confirm_callback=diff_confirm_callback,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc_dict["id"],
                "content": str(result)[:6000],
            })

    return "Reached max turns without finishing — task may be too large for one run.", plan_approved


def fan_out(task_template, targets, max_workers=8, auto_confirm=True):
    """Run run_agent() in parallel across many targets. Returns {target: result}."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(run_agent, task_template.format(target=t),
                            None, auto_confirm, None, False): t
            for t in targets
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                results[target] = future.result()
            except Exception as e:
                results[target] = f"ERROR: {e}"
    return results
```

---

## tools.py — Built-in Tools

### Design Philosophy

Two write strategies:
- **Immediate** (`write_file`, `edit_file`): writes straight to disk, no review. Best for quick single-file fixes.
- **Staged** (`stage_write_file`, `stage_edit_file` → `review_pending_changes` → `apply_pending_changes`): queues changes without touching disk, generates a full unified diff for human review, then applies everything at once. Best for multi-file refactors or anything the agent wants reviewed before committing.

### Tool Summary

| Tool | Description |
|---|---|
| `read_file` | Read file, optional line range |
| `read_files` | Read multiple files in one call (20 000 char cap) |
| `write_file` | Atomic write — immediate, no review step |
| `edit_file` | Find-and-replace, exactly one match required — immediate |
| `stage_write_file` | Queue a full-file write without touching disk |
| `stage_edit_file` | Queue a find-and-replace without touching disk; composes with prior staged edits |
| `review_pending_changes` | Full unified diff of all currently staged changes across all files |
| `apply_pending_changes` | Write every staged change to disk — requires `confirmed=True` (human-gated) |
| `discard_pending_changes` | Throw away staged changes without writing anything |
| `list_directory` | List files and folders at a path |
| `search_codebase` | Text/regex search across files (pure-Python) |
| `run_bash` | Shell — hard-blocked catastrophic patterns + soft-block confirm for destructive |
| `revert_file` | `git checkout -- <path>` to restore last committed state |
| `detect_and_run_tests` | Auto-detect and run pytest or npm test |
| `write_todos` | Create/update visible task checklist |
| `git_commit` | Stage all + commit (injection-safe subprocess list) |

### Staged Changes System

```python
_STAGED_CHANGES = {}  # path -> {"old": str, "new": str, "is_new_file": bool}
```

- `stage_write_file` / `stage_edit_file` write to `_STAGED_CHANGES` only — no disk I/O.
- Multiple `stage_edit_file` calls on the same path compose: each operates on the already-staged state.
- `review_pending_changes` generates a unified diff from old→new for every staged path.
- `apply_pending_changes(confirmed=True)` calls `write_file()` for each staged path, then clears the dict.
- `discard_pending_changes` clears the dict without writing.

### Shell Safety

Hard-blocked (will never run):
```
rm -rf /   rm -rf /*   mkfs.*   fork bomb
```

Soft-blocked (require `confirmed=True` or human callback):
```
rm -rf   git push --force   git reset --hard   drop table
mkfs   dd if=   > /dev/sd   chmod -R 777
```

### Full Code

```python
"""
tools.py — the actions the agent can actually take.

New in this version:
  - stage_write_file / stage_edit_file: queue a change without touching disk.
  - review_pending_changes: full unified diff of everything staged so far.
  - apply_pending_changes: writes every staged change to disk in one shot,
    gated behind human approval (see agent.py's diff_confirm_callback).
  - discard_pending_changes: throws away staged changes without writing them.

write_file / edit_file (immediate, no review step) are kept for quick,
single-file fixes where a full review pass is overkill.
"""

import os
import re
import subprocess
import json
import difflib


def read_file(path, line_start=None, line_end=None):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    try:
        with open(path, "r") as f:
            if line_start is None and line_end is None:
                return f.read()
            lines = f.readlines()
    except UnicodeDecodeError:
        return f"ERROR: {path} is not a text file (binary content)."
    start = max((line_start or 1) - 1, 0)
    end = line_end if line_end is not None else len(lines)
    selected = lines[start:end]
    if not selected:
        return f"ERROR: line range {line_start}-{line_end} is out of bounds."
    return "".join(selected)


def write_file(path, content):
    """Atomic write — crash-safe via temp file + os.replace()."""
    tmp_path = path + ".tmp" + str(os.getpid())
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)
    return f"Wrote {len(content)} chars to {path}."


def edit_file(path, old_text, new_text):
    """Targeted find-and-replace. Requires exactly one match. Writes immediately."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    content = read_file(path)
    occurrences = content.count(old_text)
    if occurrences == 0:
        return f"Could not find that exact text in {path}. No changes made."
    if occurrences > 1:
        return (f"ERROR: that text appears {occurrences} times in {path} — "
                f"include more context to make old_text unique.")
    new_content = content.replace(old_text, new_text)
    write_file(path, new_content)
    diff_preview = "\n".join(
        list(difflib.unified_diff(
            content.splitlines(), new_content.splitlines(),
            lineterm="", n=1
        ))[:20]
    )
    return f"Edited {path}.\n{diff_preview}"


def list_directory(path="."):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    entries = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        entries.append(("[dir] " if os.path.isdir(full) else "      ") + name)
    return "\n".join(entries) if entries else "(empty directory)"


def search_codebase(query, root=".", extensions=None, use_regex=False):
    matches = []
    pattern = None
    if use_regex:
        try:
            pattern = re.compile(query)
        except re.error as e:
            return f"ERROR: invalid regex '{query}': {e}"
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
        for fname in filenames:
            if extensions and not any(fname.endswith(ext) for ext in extensions):
                continue
            full = os.path.join(dirpath, fname)
            try:
                with open(full, "r", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        is_match = pattern.search(line) if pattern else query.lower() in line.lower()
                        if is_match:
                            matches.append(f"{full}:{lineno}: {line.strip()}")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            if len(matches) >= 100:
                return "\n".join(matches) + "\n... (truncated at 100 matches)"
    return "\n".join(matches) if matches else f"No matches found for '{query}'."


_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-rf\b", r"\bgit\s+push\s+--force\b", r"\bgit\s+reset\s+--hard\b",
    r"\bdrop\s+table\b", r"\bmkfs\b", r"\bdd\s+if=", r">\s*/dev/sd",
    r"\bchmod\s+-R\s+777\b", r":\(\)\{",
]
_HARD_BLOCKED_PATTERNS = [
    r"\brm\s+-rf\s+/\s*$", r"\brm\s+-rf\s+/\*", r"\bmkfs\.", r":\(\)\{\s*:\|:&\s*\};:",
]


def _matches_any(command, patterns):
    return any(re.search(p, command) for p in patterns)


def run_bash(command, confirmed=False, timeout=60):
    stripped = command.strip()
    if not stripped:
        return "Empty command, nothing to run."
    if _matches_any(stripped, _HARD_BLOCKED_PATTERNS):
        return "BLOCKED: this command matches a hard safety block and will never be run."
    if _matches_any(stripped, _DESTRUCTIVE_PATTERNS) and not confirmed:
        return f"CONFIRMATION_REQUIRED: '{stripped}' looks destructive."
    try:
        result = subprocess.run(
            stripped, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    output = (result.stdout or "") + (result.stderr or "")
    return f"(exit code {result.returncode})\n{output[-4000:]}"


def read_files(paths):
    if not isinstance(paths, list) or not paths:
        return "ERROR: paths must be a non-empty list."
    sections = []
    total_len = 0
    for path in paths:
        content = read_file(path)
        total_len += len(content)
        if total_len > 20000:
            sections.append(f"=== {path} ===\n(skipped — combined batch size limit reached)")
            continue
        sections.append(f"=== {path} ===\n{content}")
    return "\n\n".join(sections)


def revert_file(path):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    check = run_bash(f"git ls-files --error-unmatch {path}", confirmed=True)
    if "exit code 0" not in check:
        return f"ERROR: '{path}' is not tracked by git."
    result = run_bash(f"git checkout -- {path}", confirmed=True)
    if "exit code 0" not in result:
        return f"ERROR: revert failed:\n{result}"
    return f"Reverted {path} to its last committed state."


def detect_and_run_tests(root="."):
    has_pytest_config = (os.path.exists(os.path.join(root, "pytest.ini")) or
                         os.path.exists(os.path.join(root, "conftest.py")))
    has_test_dir = os.path.isdir(os.path.join(root, "tests"))
    has_test_files = any(
        f.startswith("test_") or f.endswith("_test.py")
        for f in os.listdir(root) if os.path.isfile(os.path.join(root, f))
    ) if os.path.isdir(root) else False
    if has_pytest_config or has_test_dir or has_test_files:
        result = run_bash("python3 -m pytest -q", confirmed=True, timeout=120)
        return f"Detected a pytest setup. Ran `python3 -m pytest -q`:\n{result}"
    package_json = os.path.join(root, "package.json")
    if os.path.exists(package_json):
        try:
            with open(package_json, "r") as f:
                pkg = json.load(f)
            if pkg.get("scripts", {}).get("test"):
                result = run_bash("npm test", confirmed=True, timeout=120)
                return f"Detected an npm test script. Ran `npm test`:\n{result}"
        except (json.JSONDecodeError, OSError):
            pass
    return "No recognizable test setup found. Nothing was run."


def write_todos(todos):
    if not isinstance(todos, list) or not todos:
        return "ERROR: todos must be a non-empty list."
    lines = []
    for i, item in enumerate(todos):
        if not isinstance(item, dict) or "content" not in item:
            return f"ERROR: todo #{i} is missing 'content'."
        status = item.get("status", "pending")
        if status not in {"pending", "in_progress", "completed"}:
            return f"ERROR: todo #{i} has invalid status '{status}'."
        marker = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}[status]
        lines.append(f"{marker} {item['content']}")
    return "Plan updated:\n" + "\n".join(lines)


def git_commit(message, add_all=True):
    """Injection-safe: uses subprocess list, not shell string interpolation."""
    if add_all:
        add_result = run_bash("git add -A", confirmed=True)
        if "exit code 0" not in add_result:
            return f"git add failed:\n{add_result}"
    try:
        result = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "git commit timed out after 30s."
    output = (result.stdout or "") + (result.stderr or "")
    return f"(exit code {result.returncode})\n{output[-4000:]}"


# ---------------------------------------------------------------------------
# Diff-before-apply: staged changes reviewed as a whole before hitting disk.
# ---------------------------------------------------------------------------

_STAGED_CHANGES = {}  # path -> {"old": str, "new": str, "is_new_file": bool}


def _current_staged_or_disk_content(path):
    if path in _STAGED_CHANGES:
        return _STAGED_CHANGES[path]["new"]
    if os.path.exists(path):
        return read_file(path)
    return None


def stage_write_file(path, content):
    existing = _current_staged_or_disk_content(path)
    is_new_file = existing is None
    old_content = existing if existing is not None else ""
    _STAGED_CHANGES[path] = {"old": old_content, "new": content, "is_new_file": is_new_file}
    diff_preview = "\n".join(
        list(difflib.unified_diff(
            old_content.splitlines(), content.splitlines(), lineterm="", n=1
        ))[:20]
    )
    tag = "new file" if is_new_file else "modified"
    return f"Staged ({tag}) {path}. Not yet written to disk.\n{diff_preview}"


def stage_edit_file(path, old_text, new_text):
    current = _current_staged_or_disk_content(path)
    if current is None:
        return f"ERROR: {path} does not exist and has no staged content. Use stage_write_file for new files."
    occurrences = current.count(old_text)
    if occurrences == 0:
        return f"Could not find that exact text in {path} (including any staged edits). No changes staged."
    if occurrences > 1:
        return (f"ERROR: that text appears {occurrences} times in {path} — "
                f"include more context to make old_text unique.")
    new_content = current.replace(old_text, new_text)
    return stage_write_file(path, new_content)


def review_pending_changes():
    if not _STAGED_CHANGES:
        return "No pending changes staged."
    sections = []
    for path, change in _STAGED_CHANGES.items():
        label = "new file" if change["is_new_file"] else "modified"
        diff_lines = list(difflib.unified_diff(
            change["old"].splitlines(), change["new"].splitlines(),
            fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
        ))
        diff_text = "\n".join(diff_lines) if diff_lines else "(no textual difference)"
        sections.append(f"--- {path} ({label}) ---\n{diff_text}")
    return "\n\n".join(sections)


def apply_pending_changes(confirmed=False):
    if not _STAGED_CHANGES:
        return "No pending changes to apply."
    if not confirmed:
        return "CONFIRMATION_REQUIRED: call review_pending_changes first, then get human approval before applying."
    written = []
    for path, change in _STAGED_CHANGES.items():
        write_file(path, change["new"])
        written.append(path)
    _STAGED_CHANGES.clear()
    return "Applied and wrote to disk:\n  " + "\n  ".join(written)


def discard_pending_changes():
    if not _STAGED_CHANGES:
        return "No pending changes to discard."
    count = len(_STAGED_CHANGES)
    _STAGED_CHANGES.clear()
    return f"Discarded {count} staged change(s). Nothing was written to disk."
```

---

## vision_tools.py — Browser & Visual Verification

Gives the agent eyes and hands: a persistent headless browser session via Playwright, a screenshot crop/zoom tool for reading small UI elements, and a pixel-diff-based visual self-verification tool for comparing rendered output against design mockups.

**Requires:** `pip install playwright pillow numpy` + `playwright install chromium`

### Features

| Tool | Description |
|---|---|
| `browser_navigate` | Open a URL in a persistent headless Chromium session (1280×800) |
| `browser_click` | Click an element by CSS selector |
| `browser_type` | Fill an input/textarea by CSS selector; optional Enter submit |
| `browser_screenshot` | Full-page screenshot → `.agent_screenshots/<label>_<ts>.png` |
| `browser_close` | Close the browser session and free resources |
| `screen_crop` | Crop a pixel region from a screenshot and upscale it (default 2×) for readability |
| `visual_self_verify` | Pixel diff between a rendered screenshot and a design mockup — % changed, verdict, red-highlight diff image |

### visual_self_verify Verdicts

| % pixels changed | Verdict |
|---|---|
| < 2% | Close match — likely fine |
| 2–10% | Minor differences — spacing/color tweaks may be needed |
| > 10% | Significant differences — layout or content likely wrong |

### Lazy Browser Session

`_browser_state` is a module-level dict. `_get_page()` starts `playwright → chromium → page` on first call and reuses them for all subsequent calls. `browser_close()` tears everything down.

### Full Code

```python
"""
vision_tools.py — gives the agent eyes: browser control, screenshot cropping,
and visual self-verification against a design mockup.

Requires:
    pip install playwright pillow numpy
    playwright install chromium
"""

import base64
import io
import os
import time

from tools import write_file

SCREENSHOT_DIR = ".agent_screenshots"


def _ensure_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


_browser_state = {"playwright": None, "browser": None, "page": None}


def _get_page():
    """Lazily launch a persistent headless browser + page."""
    if _browser_state["page"] is not None:
        return _browser_state["page"]

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    _browser_state["playwright"] = pw
    _browser_state["browser"] = browser
    _browser_state["page"] = page
    return page


def browser_navigate(url):
    """Open a URL in the persistent browser session."""
    try:
        page = _get_page()
        page.goto(url, timeout=20000, wait_until="load")
        return f"Navigated to {url}. Page title: {page.title()}"
    except Exception as e:
        return f"ERROR: could not navigate to {url}: {e}"


def browser_click(selector):
    """Click an element matched by a CSS selector."""
    try:
        page = _get_page()
        page.click(selector, timeout=10000)
        return f"Clicked '{selector}'."
    except Exception as e:
        return f"ERROR: could not click '{selector}': {e}"


def browser_type(selector, text, submit=False):
    """Type text into an input/textarea matched by a CSS selector."""
    try:
        page = _get_page()
        page.fill(selector, text, timeout=10000)
        if submit:
            page.press(selector, "Enter")
        return f"Typed into '{selector}'{' and submitted' if submit else ''}."
    except Exception as e:
        return f"ERROR: could not type into '{selector}': {e}"


def browser_screenshot(label="screenshot", full_page=True):
    """
    Take a screenshot of the current page state.
    Returns the saved file path (also viewable by the agent's vision).
    """
    try:
        _ensure_dir()
        page = _get_page()
        path = os.path.join(SCREENSHOT_DIR, f"{label}_{int(time.time())}.png")
        page.screenshot(path=path, full_page=full_page)
        return f"Screenshot saved to {path}"
    except Exception as e:
        return f"ERROR: could not take screenshot: {e}"


def browser_close():
    """Close the browser session and free resources."""
    try:
        if _browser_state["browser"]:
            _browser_state["browser"].close()
        if _browser_state["playwright"]:
            _browser_state["playwright"].stop()
        _browser_state.update({"playwright": None, "browser": None, "page": None})
        return "Browser session closed."
    except Exception as e:
        return f"ERROR closing browser: {e}"


def screen_crop(image_path, left, top, right, bottom, zoom=2, label="crop"):
    """
    Crop a region out of a screenshot and upscale it so small text/buttons
    become legible. Coordinates are pixels in the original image.
    """
    if not os.path.exists(image_path):
        return f"ERROR: {image_path} does not exist."
    try:
        from PIL import Image
    except ImportError:
        return "ERROR: Pillow is not installed. Run: pip install pillow"

    try:
        _ensure_dir()
        img = Image.open(image_path)
        box = (left, top, right, bottom)
        cropped = img.crop(box)
        w, h = cropped.size
        if w <= 0 or h <= 0:
            return f"ERROR: crop box {box} produced an empty region."
        cropped = cropped.resize((w * zoom, h * zoom), Image.LANCZOS)
        out_path = os.path.join(SCREENSHOT_DIR, f"{label}_{int(time.time())}.png")
        cropped.save(out_path)
        return f"Cropped region {box} from {image_path}, zoomed {zoom}x, saved to {out_path}"
    except Exception as e:
        return f"ERROR: crop failed: {e}"


def visual_self_verify(rendered_path, mockup_path, diff_threshold=30, label="diff"):
    """
    Compare a screenshot of newly-built UI against a reference mockup image.
    Produces a diff-highlight image and a plain-text summary of how different
    they are, so the agent can decide whether to keep iterating.
    """
    if not os.path.exists(rendered_path):
        return f"ERROR: {rendered_path} does not exist."
    if not os.path.exists(mockup_path):
        return f"ERROR: {mockup_path} does not exist."

    try:
        from PIL import Image, ImageChops
        import numpy as np
    except ImportError:
        return "ERROR: Pillow and numpy are required. Run: pip install pillow numpy"

    try:
        _ensure_dir()
        rendered = Image.open(rendered_path).convert("RGB")
        mockup = Image.open(mockup_path).convert("RGB")

        if rendered.size != mockup.size:
            rendered = rendered.resize(mockup.size, Image.LANCZOS)

        diff = ImageChops.difference(rendered, mockup)
        diff_array = np.array(diff)
        gray_diff = diff_array.mean(axis=2)

        changed_pixels = int((gray_diff > diff_threshold).sum())
        total_pixels = gray_diff.shape[0] * gray_diff.shape[1]
        pct_changed = round(100 * changed_pixels / total_pixels, 2)

        highlight = np.array(rendered).copy()
        mask = gray_diff > diff_threshold
        highlight[mask] = [255, 0, 0]
        out_path = os.path.join(SCREENSHOT_DIR, f"{label}_{int(time.time())}.png")
        Image.fromarray(highlight).save(out_path)

        verdict = (
            "Close match — likely fine." if pct_changed < 2 else
            "Minor differences — spacing/color tweaks may be needed." if pct_changed < 10 else
            "Significant differences — layout or content likely wrong."
        )

        return (
            f"Compared {rendered_path} vs {mockup_path}: {pct_changed}% of pixels differ "
            f"beyond threshold={diff_threshold}. {verdict} "
            f"Diff-highlight image saved to {out_path}"
        )
    except Exception as e:
        return f"ERROR: comparison failed: {e}"


VISION_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "browser_navigate",
        "description": "Open a URL in a headless browser session, like a human tester would.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "browser_click",
        "description": "Click an element in the current browser page via a CSS selector.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"},
        }, "required": ["selector"]},
    }},
    {"type": "function", "function": {
        "name": "browser_type",
        "description": "Type text into an input/textarea in the current browser page.",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "submit": {"type": "boolean", "default": False},
        }, "required": ["selector", "text"]},
    }},
    {"type": "function", "function": {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current browser page state for visual inspection.",
        "parameters": {"type": "object", "properties": {
            "label": {"type": "string", "default": "screenshot"},
            "full_page": {"type": "boolean", "default": True},
        }},
    }},
    {"type": "function", "function": {
        "name": "browser_close",
        "description": "Close the current headless browser session.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "screen_crop",
        "description": "Crop and zoom into a region of a screenshot when it is too blurry, "
                        "cluttered, or small to read a specific button or error message clearly.",
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string"},
            "left": {"type": "integer"},
            "top": {"type": "integer"},
            "right": {"type": "integer"},
            "bottom": {"type": "integer"},
            "zoom": {"type": "integer", "default": 2},
            "label": {"type": "string", "default": "crop"},
        }, "required": ["image_path", "left", "top", "right", "bottom"]},
    }},
    {"type": "function", "function": {
        "name": "visual_self_verify",
        "description": "Compare a screenshot of just-built UI against a design mockup image to "
                        "check colors, spacing, and layout match before declaring frontend work done.",
        "parameters": {"type": "object", "properties": {
            "rendered_path": {"type": "string"},
            "mockup_path": {"type": "string"},
            "diff_threshold": {"type": "integer", "default": 30},
            "label": {"type": "string", "default": "diff"},
        }, "required": ["rendered_path", "mockup_path"]},
    }},
]

VISION_TOOL_FUNCTIONS = {
    "browser_navigate": browser_navigate,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_screenshot": browser_screenshot,
    "browser_close": browser_close,
    "screen_crop": screen_crop,
    "visual_self_verify": visual_self_verify,
}
```

---

## provider_pool.py — LLM Rotation

### Features

| Feature | Description |
|---|---|
| **Priority order** | Cerebras → OpenRouter → Groq |
| **Multi-key rotation** | `KEY`, `KEY_2`, `KEY_3` … loaded automatically |
| **Retry-After aware** | Reads header on 429 — both integer seconds and HTTP-date forms |
| **Clamped cooldown** | 1s min, 300s max — prevents frozen slots from bad headers |
| **Thread-safe** | `threading.Lock()` on all slot state |
| **Tool-call passthrough** | Forwards `tools` + `tool_choice` |
| **Groq fallback tier** | All primary slots exhausted → try every Groq key |

### Full Code

```python
"""
provider_pool.py — multi-key LLM rotation with native tool-calling support.

UPDATED: Retry-After aware cooldown.
  - On a 429, reads the Retry-After header (seconds, or an HTTP-date) if present
    and cools the slot down for exactly that long instead of a flat 60s.
  - Falls back to the previous flat default only when no usable header is sent.
"""

import os
import time
import threading
import requests
import json
from email.utils import parsedate_to_datetime


def _load_keys(env_prefix):
    keys = []
    primary = os.environ.get(env_prefix)
    if primary:
        keys.append(primary)
    i = 2
    while True:
        k = os.environ.get(f"{env_prefix}_{i}")
        if not k:
            break
        keys.append(k)
        i += 1
    return keys


CEREBRAS_KEYS = _load_keys("CEREBRAS_API_KEY")
GROQ_KEYS = _load_keys("GROQ_API_KEY")
OPENROUTER_KEYS = _load_keys("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-120b")

DEFAULT_RATE_LIMIT_COOLDOWN = 60
DEFAULT_NETWORK_ERROR_COOLDOWN = 10
MIN_COOLDOWN = 1
MAX_COOLDOWN = 300


def _build_pool():
    pool = [{"provider": "cerebras", "key": k, "model": CEREBRAS_MODEL} for k in CEREBRAS_KEYS]
    pool += [{"provider": "openrouter", "key": k, "model": OPENROUTER_MODEL} for k in OPENROUTER_KEYS]
    return pool


PROVIDER_POOL = _build_pool()
_key_lock = threading.Lock()
_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(PROVIDER_POOL))}
_groq_key_lock = threading.Lock()
_groq_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(GROQ_KEYS))}


def _parse_retry_after(resp, default_seconds=DEFAULT_RATE_LIMIT_COOLDOWN):
    header = resp.headers.get("Retry-After") if resp is not None else None
    if not header:
        return default_seconds
    header = header.strip()
    try:
        return max(MIN_COOLDOWN, min(MAX_COOLDOWN, float(header)))
    except ValueError:
        pass
    try:
        target_dt = parsedate_to_datetime(header)
        seconds = target_dt.timestamp() - time.time()
        return max(MIN_COOLDOWN, min(MAX_COOLDOWN, seconds))
    except (TypeError, ValueError):
        return default_seconds


def _pick_available_index(state, lock, pool_len):
    now = time.time()
    with lock:
        not_cooling = [i for i in range(pool_len) if state[i]["cooldown_until"] <= now]
        idle = [i for i in not_cooling if not state[i]["in_use"]]
        if idle:
            chosen = idle[0]
        elif not_cooling:
            chosen = not_cooling[0]
        else:
            chosen = min(range(pool_len), key=lambda i: state[i]["cooldown_until"])
        state[chosen]["in_use"] = True
        return chosen


def _mark_rate_limited(state, lock, index, cooldown_seconds=DEFAULT_RATE_LIMIT_COOLDOWN):
    with lock:
        state[index]["cooldown_until"] = time.time() + cooldown_seconds


def ask_ai(messages, tools=None, tool_choice="auto", max_tokens=4096):
    if not PROVIDER_POOL and not GROQ_KEYS:
        return {"error": "No API keys found in environment."}
    last_error = "unknown failure"

    for _ in range(len(PROVIDER_POOL)):
        idx = _pick_available_index(_key_state, _key_lock, len(PROVIDER_POOL))
        slot = PROVIDER_POOL[idx]
        try:
            url = ("https://api.cerebras.ai/v1/chat/completions"
                   if slot["provider"] == "cerebras"
                   else "https://openrouter.ai/api/v1/chat/completions")
            payload = {"model": slot["model"], "messages": messages, "max_tokens": max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            try:
                resp = requests.post(url,
                    headers={"Authorization": f"Bearer {slot['key']}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=60)
            except requests.exceptions.RequestException as e:
                _mark_rate_limited(_key_state, _key_lock, idx, DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = f"could not reach {slot['provider']} ({e})"
                continue
            if resp.status_code == 429:
                cooldown = _parse_retry_after(resp)
                _mark_rate_limited(_key_state, _key_lock, idx, cooldown)
                last_error = f"rate limited on {slot['provider']} (cooling {cooldown:.0f}s)"
                continue
            try:
                data = resp.json()
            except Exception:
                _mark_rate_limited(_key_state, _key_lock, idx, DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = f"could not parse {slot['provider']} response"
                continue
            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    cooldown = _parse_retry_after(resp)
                    _mark_rate_limited(_key_state, _key_lock, idx, cooldown)
                    last_error = f"{slot['provider']}: {data}"
                    continue
                return {"error": f"API error from {slot['provider']}: {data}"}
            return data["choices"][0]["message"]
        finally:
            with _key_lock:
                _key_state[idx]["in_use"] = False

    for _ in range(len(GROQ_KEYS)):
        idx = _pick_available_index(_groq_key_state, _groq_key_lock, len(GROQ_KEYS))
        api_key = GROQ_KEYS[idx]
        try:
            payload = {"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            try:
                resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json=payload, timeout=60)
            except requests.exceptions.RequestException as e:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = f"could not reach Groq ({e})"
                continue
            if resp.status_code == 429:
                cooldown = _parse_retry_after(resp)
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown)
                last_error = f"rate limited on Groq (cooling {cooldown:.0f}s)"
                continue
            try:
                data = resp.json()
            except Exception:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = "could not parse Groq response"
                continue
            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    cooldown = _parse_retry_after(resp)
                    _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown)
                    last_error = f"Groq: {data}"
                    continue
                return {"error": f"Groq error: {data}"}
            return data["choices"][0]["message"]
        finally:
            with _groq_key_lock:
                _groq_key_state[idx]["in_use"] = False

    return {"error": last_error}
```

---

## task_memory.py — Cross-Session Memory

### Features

| Feature | Description |
|---|---|
| **Persistence** | `.agent_memory.json`, up to 200 entries |
| **Importance scores (1–10)** | Auto-inferred from keyword heuristics at save time |
| **Semantic retrieval** | Gemini embeddings (cosine > 0.55 threshold) when `GEMINI_API_KEY` set |
| **Keyword fallback** | Word-overlap ranking when no embeddings available |
| **Weighted ranking** | 60% cosine similarity + 40% importance score |
| **`[HIGH IMPORTANCE]` tag** | Appended in prompt for entries with importance ≥ 8 |
| **Backfill** | Old entries without `importance` get it inferred on first load |
| **Crash-safe writes** | temp file + `os.replace()` |

### High-Importance Keywords (+3)
`critical  bug  fixed  broke  regression  security  data loss  crash  failed  important  never do  do not  gotcha  careful  corrupt  irreversible`

### Low-Importance Keywords (−2)
`typo  minor  cosmetic  formatting  rename`

### Full Code

```python
"""
task_memory.py — persistent memory across sessions, per project.

UPDATED: importance-weighted ranking.
"""

import json
import math
import os
import re
import time
import requests

MEMORY_FILE = ".agent_memory.json"
MAX_STORED = 200
MAX_RETRIEVED = 5
DEFAULT_IMPORTANCE = 5

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GEMINI_EMBED_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_EMBED_MODEL}:embedContent?key={{key}}"
)

_HIGH_IMPORTANCE_MARKERS = [
    "critical", "bug", "fixed", "broke", "regression", "security", "data loss",
    "crash", "failed", "important", "never do", "do not", "gotcha", "careful",
    "corrupt", "irreversible",
]
_LOW_IMPORTANCE_MARKERS = ["typo", "minor", "cosmetic", "formatting", "rename"]


def _embed(text):
    if not GEMINI_API_KEY:
        return None
    try:
        resp = requests.post(
            GEMINI_EMBED_URL.format(key=GEMINI_API_KEY),
            json={"model": f"models/{GEMINI_EMBED_MODEL}",
                  "content": {"parts": [{"text": text}]}},
            timeout=15,
        )
        data = resp.json()
        return data.get("embedding", {}).get("values")
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


def _cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _infer_importance(task, summary):
    text = f"{task} {summary}".lower()
    score = DEFAULT_IMPORTANCE
    if any(marker in text for marker in _HIGH_IMPORTANCE_MARKERS):
        score += 3
    if any(marker in text for marker in _LOW_IMPORTANCE_MARKERS):
        score -= 2
    return max(1, min(10, score))


def _load(memory_path):
    if not os.path.exists(memory_path):
        return []
    try:
        with open(memory_path, "r") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    changed = False
    for e in entries:
        if "importance" not in e:
            e["importance"] = _infer_importance(e.get("task", ""), e.get("summary", ""))
            changed = True
    if changed:
        _save(memory_path, entries)
    return entries


def _save(memory_path, entries):
    tmp = memory_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries[-MAX_STORED:], f, indent=2)
    os.replace(tmp, memory_path)


def add_task_summary(task, summary, memory_path=MEMORY_FILE, importance=None):
    entries = _load(memory_path)
    score = importance if importance is not None else _infer_importance(task, summary)
    entries.append({
        "task": task,
        "summary": summary,
        "timestamp": time.time(),
        "importance": max(1, min(10, score)),
        "embedding": _embed(task),
    })
    _save(memory_path, entries)


def retrieve_relevant(task, memory_path=MEMORY_FILE, top_k=MAX_RETRIEVED):
    entries = _load(memory_path)
    if not entries:
        return []
    query_embedding = _embed(task)
    task_words = set(task.lower().split())

    if query_embedding:
        scored = [(e, _cosine_similarity(query_embedding, e["embedding"]))
                  for e in entries if e.get("embedding")]
        relevant = [(e, sim) for e, sim in scored if sim > 0.55]
        if relevant:
            relevant.sort(
                key=lambda pair: (pair[1] * 0.6) + (pair[0]["importance"] / 10 * 0.4),
                reverse=True,
            )
            return [e for e, _ in relevant[:top_k]]

    def keyword_score(entry):
        overlap = len(task_words & set(entry["task"].lower().split()))
        return (entry.get("importance", DEFAULT_IMPORTANCE), overlap)

    candidates = [e for e in entries if len(task_words & set(e["task"].lower().split())) > 0]
    candidates.sort(key=keyword_score, reverse=True)
    return candidates[:top_k]


def format_for_prompt(entries):
    if not entries:
        return ""
    lines = ["Relevant memory from past sessions in this project:"]
    for e in entries:
        tag = " [HIGH IMPORTANCE]" if e.get("importance", DEFAULT_IMPORTANCE) >= 8 else ""
        lines.append(f"- Task: \"{e['task']}\" -> {e['summary']}{tag}")
    return "\n".join(lines)
```

---

## custom_tool_registry.py — Self-Created Tools

### Features

| Feature | Description |
|---|---|
| **`create_tool`** | Validates, saves to `custom_tools.py` + `custom_tools_schema.json`, hot-reloads |
| **Import allowlist** | Only stdlib safe modules; `os`, `subprocess`, `socket` etc. blocked |
| **SIGALRM timeout** | 10s hard limit on validation `exec()` AND every runtime call |
| **`_wrap_with_timeout`** | Wraps every loaded function; catches `ToolTimeoutError` + generic exceptions |
| **Unix fallback** | No `SIGALRM` on non-Unix — runs without timeout guard |
| **`load_custom_tools()`** | Hot-reloads module; returns `(schema, functions, errors)` |

See [custom_tool_registry.py full code in previous section](#) — unchanged from last sync. Full code included below for completeness.

```python
# (full code unchanged — see previous AGENT_OVERVIEW.md version or read the file directly)
```

---

## custom_tools.py — Agent-Created Tools

Auto-generated by `create_tool`. All calls wrapped with 10s SIGALRM timeout.

| Tool | Description |
|---|---|
| `count_python_lines` | Returns the line count of a `.py` file as a string |

---

## run_logger.py — Tool Call Logger

> Note: `run_logger` is not currently imported by `agent.py`. It exists as a standalone utility module for external/debugging use.

JSONL structured logging of every tool call. Dependency-free, best-effort.

| Feature | Description |
|---|---|
| **JSONL format** | One JSON record per line in `.agent_runs.jsonl` |
| **Per-call latency** | `latency_ms` field |
| **Error flag** | `"error": true` when result starts with `"ERROR"` |
| **Previews capped** | Args and result at 300 chars each |
| **Best-effort** | `OSError` silently swallowed |
| **`summarize_recent(n)`** | Returns last `n` records as list of dicts |

### Log Record Format

```json
{
  "timestamp": 1751234567.89,
  "tool": "write_file",
  "args_preview": "{\"path\": \"auth.py\", ...}",
  "latency_ms": 12.4,
  "result_preview": "Wrote 842 chars to auth.py.",
  "error": false
}
```

---

## structural_search.py — AST Code Search

> Note: `structural_search` is not currently imported by `agent.py`. It exists as a standalone utility module available for direct use or future re-integration.

AST-aware Python code search — finds definitions, call sites, and all references by parsing the actual syntax tree.

| Tool | Description |
|---|---|
| `find_definition` | Where a function/class is defined — returns file:line + signature + docstring |
| `find_callers` | Every call site of a function/method |
| `find_references` | Every usage — calls, reads, writes, attribute access, import aliases |
| `outline_file` | Structural map of one file — all classes, methods, functions with line numbers |

---

## lsp_client.py — Language Server Client

> Note: `lsp_client` is not currently imported by `agent.py`. It exists as a standalone utility module.

Real LSP client that spawns `pylsp` as a subprocess, speaks JSON-RPC over stdio.

| Tool | Description |
|---|---|
| `lsp_get_diagnostics` | Semantic errors, undefined names, type mismatches, unused imports |
| `lsp_hover` | Type/docstring at a 0-indexed position |
| `lsp_go_to_definition` | Cross-file, cross-import definition — more capable than AST-only |
| `lsp_restart` | Recover if server wedges |

**Requires:** `pip install python-lsp-server`

---

## file_search.py — Glob File Search

> Note: `file_search` is not currently imported by `agent.py`. It exists as a standalone utility module.

Find files by name/path pattern instead of content. Results sorted newest-modified-first.

| Feature | Description |
|---|---|
| **Glob patterns** | `*.py`, `**/*.py`, `src/**/test_*.py` |
| **Newest-first sort** | Most recently modified file first |
| **200 result cap** | Truncates with message |

---

## firebase_tools.py — Firebase Scaffolding

Unchanged. Generates `firebase-config.js`, `auth.js`, `db.js`, `firestore.rules` for any web app needing user login and per-user Firestore data. Returns a manual-steps checklist for the Firebase console.

---

## github_tools.py — GitHub Integration

Unchanged. `git_push`, `create_branch`, `open_pull_request`, `list_open_issues` via GitHub REST API. Auto-detects `owner/repo` from HTTPS/SSH remote URLs. Requires `GITHUB_TOKEN`.

---

## meta_builder.py — Simulation Generator

Unchanged. Generates a complete N-agent simulation system from one theme prompt using parallel LLM batch calls. Outputs `agents.json`, `memory.py`, `tick_engine.py`, `viewer.py`.

---

## tick_engine.py — Simulation Runtime

Unchanged. Hourly tick loop — for each agent: build prompt → call LLM → log to `event_log.json` → store memory. Configurable via `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`.

---

## memory.py — Per-Agent Memory Store

Unchanged. JSON-backed per-agent store. Importance-first retrieval, 200-entry cap, crash-safe writes.

---

## director.py — Event Injection

Unchanged. `inject_event` broadcasts to all agents; `inject_event_for_agent` targets one. Both store at `importance=10`.

---

## Utility Files

### calc.py

```python
def divide(a, b):
    if not isinstance(a, (int, float)):
        raise TypeError(f"Argument 'a' must be a number, got {type(a).__name__}")
    if not isinstance(b, (int, float)):
        raise TypeError(f"Argument 'b' must be a number, got {type(b).__name__}")
    if b == 0:
        return None
    return a / b
```

### greeter.py

```python
def greet(name: str) -> str:
    """Return a greeting string for the given name."""
    return "Hello, " + name
```

### mathutils.py

```python
def square(x: int) -> int:
    """Calculate the square of an integer (x * x)."""
    return x * x
```

### shapes.py

```python
import math

class Circle:
    def __init__(self, radius: float):
        if radius < 0:
            raise ValueError("Radius cannot be negative")
        self.radius = radius
    def area(self) -> float:
        return math.pi * (self.radius ** 2)

class Square:
    def __init__(self, side: float):
        if side < 0:
            raise ValueError("Side length cannot be negative")
        self.side = side
    def area(self) -> float:
        return self.side ** 2
```

### test_calc.py

```python
import unittest
import calc

class TestCalc(unittest.TestCase):
    def test_divide_normal(self):
        self.assertEqual(calc.divide(10, 2), 5)
        self.assertEqual(calc.divide(-4, 2), -2)
        self.assertEqual(calc.divide(5.0, 2), 2.5)
    def test_divide_by_zero(self):
        self.assertIsNone(calc.divide(10, 0))
    def test_divide_invalid_type(self):
        with self.assertRaises(TypeError):
            calc.divide('10', 2)

if __name__ == '__main__':
    unittest.main()
```

---

## Environment Variables

| Variable | Required | Used By | Description |
|---|---|---|---|
| `CEREBRAS_API_KEY` | Recommended | `provider_pool.py` | Primary (fastest) LLM provider |
| `CEREBRAS_API_KEY_2` … `_N` | Optional | `provider_pool.py` | Additional Cerebras keys |
| `OPENROUTER_API_KEY` | Optional | `provider_pool.py` | Secondary LLM provider |
| `OPENROUTER_API_KEY_2` … `_3` | Optional | `provider_pool.py` | Additional OpenRouter keys |
| `GROQ_API_KEY` | Optional | `provider_pool.py` | Fallback LLM provider |
| `GEMINI_API_KEY` | Optional | `task_memory.py` | Enables semantic embedding-based memory retrieval |
| `GITHUB_TOKEN` | Optional | `github_tools.py` | Required for PR creation and issue listing |
| `LLM_API_KEY` | Optional | `tick_engine.py` | API key for simulation tick engine |
| `LLM_BASE_URL` | Optional | `tick_engine.py` | Override endpoint (default: OpenRouter) |
| `LLM_MODEL` | Optional | `tick_engine.py` | Override model (default: `qwen/qwen3-coder`) |
| `CEREBRAS_MODEL` | Optional | `provider_pool.py` | Override default Cerebras model |
| `GROQ_MODEL` | Optional | `provider_pool.py` | Override default Groq model |
| `OPENROUTER_MODEL` | Optional | `provider_pool.py` | Override default OpenRouter model |

At least one of `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, or `GROQ_API_KEY` must be set.

---

## Full System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                             cli.py                                   │
│                                                                      │
│  python cli.py "task"          one-shot                              │
│  python cli.py                 interactive Conversation              │
│  python cli.py --plan "task"   plan mode (any of the above)          │
│  echo "task" | python cli.py   stdin piping (any of the above)       │
│                                                                      │
│  Callbacks wired up:                                                 │
│    confirm_callback  → _ask_confirmation()   (destructive bash)      │
│    plan_confirm_callback → _ask_plan_approval()  (submit_plan)       │
│    diff_confirm_callback → _ask_diff_approval()  (apply_pending)     │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                     agent.py: run_agent() / Conversation.send()
                              │
                     _load_project_context()  ← AGENT.md / CLAUDE.md
                     task_memory.retrieve_relevant()
                     _run_loop()
                              │
                     ask_ai() → provider_pool.py
                     Cerebras → OpenRouter → Groq
                     Retry-After-aware cooldowns, thread-safe
                              │ tool_calls
                     _execute_tool_call()
                              │
          ┌───────────────────┴──────────────────────────────────┐
          │                   Tool Registry                      │
          │                                                      │
          │  tools.py          Immediate writes:                 │
          │    write_file        atomic, no review               │
          │    edit_file         find-replace, no review         │
          │  tools.py          Staged writes:                    │
          │    stage_write_file  → _STAGED_CHANGES dict          │
          │    stage_edit_file   composes with prior staged      │
          │    review_pending_changes  → unified diff string     │
          │    apply_pending_changes   ← diff_confirm_callback   │
          │    discard_pending_changes                           │
          │  tools.py          Other:                            │
          │    run_bash          hard-block + confirm            │
          │    revert_file       git checkout only               │
          │    search_codebase   pure-Python                     │
          │                                                      │
          │  vision_tools      browser_navigate / click / type  │
          │  (Playwright)      browser_screenshot               │
          │                    screen_crop (Pillow zoom)         │
          │                    visual_self_verify (pixel diff)   │
          │                                                      │
          │  submit_plan       plan mode gating                  │
          │    ← plan_confirm_callback (approved/feedback)       │
          │    blocks write tools until approved                 │
          │    defense-in-depth: blocked even if model ignores   │
          │                                                      │
          │  delegate_subagent fresh _run_loop, no recursion     │
          │    sub-agent cannot call delegate_subagent           │
          │    returns final summary only (not tool history)     │
          │                                                      │
          │  firebase_tools    scaffold_firebase_app             │
          │  github_tools      push/branch/PR/issues             │
          │  meta_builder      build_agent_system                │
          │  create_tool       write + save new tools            │
          │  custom_tools      agent-created (10s SIGALRM each)  │
          └──────────────────────────────────────────────────────┘
                              │ result → messages
                     (repeat up to MAX_TURNS=40)
                              │
              task_memory.add_task_summary()
              .agent_memory.json  (200 entries max)
              importance-weighted: 60% cosine sim + 40% importance

─────────────────────────────────────────────────────────────────────────

Standalone utility modules (importable, not in agent.py's registry):

  run_logger.py       log_tool_call() → .agent_runs.jsonl
                      summarize_recent(n) → last n records

  structural_search   find_definition / find_callers /
                      find_references / outline_file
                      (AST-aware, Python-only, pure stdlib)

  lsp_client.py       lsp_get_diagnostics / lsp_hover /
                      lsp_go_to_definition / lsp_restart
                      (spawns pylsp subprocess, JSON-RPC)

  file_search.py      glob_files(pattern) → newest-first paths

─────────────────────────────────────────────────────────────────────────

Fan-out (parallel external tasks):
  fan_out("Review {target} for bugs.", ["a.py", "b.py", "c.py"])
    └─ ThreadPoolExecutor (≤8 workers)
         └─ run_agent() per target (use_memory=False)
         └─ {target: result} merged

Sub-agent delegation (sequential, called by agent mid-task):
  delegate_subagent("Investigate why auth.py fails on token expiry")
    └─ fresh _run_loop (max 15 turns by default)
    └─ reduced tool set (no delegate_subagent)
    └─ returns plain-text summary only

─────────────────────────────────────────────────────────────────────────

Simulation subsystem (independent of main agent loop):

  meta_builder.py  → build_agent_system(theme, count)
                     parallel LLM batches → agents.json
                     writes tick_engine.py, memory.py, viewer.py

  tick_engine.py   → hourly tick loop
                     memory.retrieve_relevant()
                     call_llm() → log_event() → event_log.json
                     memory.add(importance=5)

  director.py      → inject_event()            all agents, importance=10
                   → inject_event_for_agent()   one agent, importance=10

  viewer.py        → Flask :8080, reads event_log.json
                     auto-refresh every 5s, WORLD EVENT styling
```
