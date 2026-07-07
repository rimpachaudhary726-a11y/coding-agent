# Coding Agent — Full Documentation

A self-directing coding agent that reads, writes, edits, and runs code via LLM tool-calling. It rotates across multiple API providers, maintains persistent memory across sessions, supports parallel fan-out for multi-file tasks, can create its own new tools at runtime, and includes a full N-agent simulation engine.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Entry Point — `cli.py`](#entry-point--clipy)
3. [Core Agent Loop — `agent.py`](#core-agent-loop--agentpy)
4. [Tool Definitions — `tools.py`](#tool-definitions--toolspy)
5. [Provider Pool — `provider_pool.py`](#provider-pool--provider_poolpy)
6. [Task Memory — `task_memory.py`](#task-memory--task_memorypy)
7. [Custom Tool Registry — `custom_tool_registry.py`](#custom-tool-registry--custom_tool_registrypy)
8. [Custom Tools — `custom_tools.py`](#custom-tools--custom_toolspy)
9. [Firebase Tools — `firebase_tools.py`](#firebase-tools--firebase_toolspy)
10. [GitHub Tools — `github_tools.py`](#github-tools--github_toolspy)
11. [Meta Builder — `meta_builder.py`](#meta-builder--meta_builderpy)
12. [Tick Engine — `tick_engine.py`](#tick-engine--tick_enginepy)
13. [Agent Memory — `memory.py`](#agent-memory--memorypy)
14. [Director — `director.py`](#director--directorpy)
15. [Environment Variables & API Keys](#environment-variables--api-keys)
16. [How Everything Connects](#how-everything-connects)

---

## Project Structure

```
.
├── cli.py                    # User-facing entry point (interactive + one-shot modes)
├── agent.py                  # Core reasoning loop, fan-out, Conversation class
├── tools.py                  # All built-in tool implementations + OpenAI-compatible schema
├── provider_pool.py          # Multi-key LLM rotation (Cerebras → OpenRouter → Groq)
├── task_memory.py            # Persistent cross-session memory with semantic retrieval
├── custom_tool_registry.py   # Agent self-creates, validates, and saves new tools
├── custom_tools.py           # Auto-generated file — tools the agent has created itself
├── firebase_tools.py         # Generates Firebase auth + Firestore boilerplate
├── github_tools.py           # Git push, branch, PR, issue listing via GitHub REST API
├── meta_builder.py           # Generates a full N-agent simulation system from one prompt
├── tick_engine.py            # Drives the simulation tick-by-tick (one hour per tick)
├── memory.py                 # Per-agent memory store used by tick_engine
├── director.py               # Inject world/personal events into a running simulation
└── requirements.txt          # Python dependencies
```

---

## Entry Point — `cli.py`

The installable CLI. Supports two modes of operation.

### Usage

```bash
# Interactive chat loop (full conversation memory across turns)
python cli.py

# One-shot task (runs once, exits)
python cli.py "fix the bug in auth.py"
```

### Features

| Feature | Description |
|---|---|
| Interactive mode | Full `Conversation` object — message history grows across turns |
| One-shot mode | `run_agent()` call, exits after the task completes |
| Live step rendering | Streams each agent turn to the terminal as it happens |
| Todo display | `write_todos` calls rendered as a formatted checklist with status icons |
| Destructive confirmation | Pauses and asks the human before any destructive shell command runs |
| `new` command | Resets the conversation without restarting the process |
| Clean exit | Handles `EOFError` and `KeyboardInterrupt` gracefully |

### Code

```python
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
                    pass

            print(f"   🔧 {name}({args})")


def _ask_confirmation(command):
    """Real pause — asks you directly before a destructive command runs."""
    print(f"\n⚠️  About to run a potentially destructive command:")
    print(f"   {command}")
    answer = input("   Allow this? [y/N] ").strip().lower()
    return answer == "y"


def main():
    print("=== Coding Agent CLI ===")
    print("Type your task, or 'quit' to exit.\n")

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
        result = conversation.send(task)
        print(f"\n✅ {result}")


if __name__ == "__main__":
    main()
```

---

## Core Agent Loop — `agent.py`

The brain. Sends the conversation + tool schema to the LLM, executes returned tool calls, feeds results back in, and repeats until the model replies with plain text or `MAX_TURNS` is hit.

### Features

| Feature | Description |
|---|---|
| Tool-calling loop | Up to 40 turns of LLM ↔ tool execution |
| `run_agent()` | Single-shot task runner; fresh conversation each call |
| `Conversation` class | Stateful multi-turn session with growing message history |
| Fan-out | Parallel `run_agent()` calls across multiple targets via `ThreadPoolExecutor` |
| Task memory | Retrieves relevant past-session summaries; saves a new one when done |
| Custom tool loading | Loads agent-created tools from `custom_tools.py` at startup |
| Live tool refresh | After `create_tool` runs, new tool is usable on the very next turn |
| Empty-response guard | Detects stalled/truncated responses and nudges the model to continue |
| Firebase integration | `scaffold_firebase_app` tool merged from `firebase_tools.py` |
| GitHub integration | `git_push`, `create_branch`, `open_pull_request`, `list_open_issues` from `github_tools.py` |
| Meta-builder tool | `build_agent_system` generates complete N-agent simulation systems |

### System Prompt Behaviour

The system prompt instructs the agent to:
- Investigate before changing anything; make the smallest correct change
- Check project root before searching subfolders
- Use `write_todos` for tasks with 3+ distinct steps
- Run tests before declaring a task done
- Use `revert_file` when an edit makes things worse
- Create new tools only when no existing tool can do the job
- Build a diagnostic tool rather than guess blindly on a stubborn bug

### Code

```python
"""
agent.py — the core reasoning loop, plus fan-out for big tasks.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from provider_pool import ask_ai
from tools import TOOL_SCHEMA, TOOL_FUNCTIONS, _matches_any, _DESTRUCTIVE_PATTERNS
from firebase_tools import FIREBASE_TOOL_SCHEMA, FIREBASE_TOOL_FUNCTIONS
from github_tools import GITHUB_TOOL_SCHEMA, GITHUB_TOOL_FUNCTIONS
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

_custom_schema, _custom_functions, _custom_load_errors = load_custom_tools()
for _err in _custom_load_errors:
    print(f"[custom tools] {_err}")

TOOL_SCHEMA = (
    TOOL_SCHEMA + FIREBASE_TOOL_SCHEMA + GITHUB_TOOL_SCHEMA
    + META_BUILDER_TOOL_SCHEMA + CREATE_TOOL_SCHEMA + _custom_schema
)
TOOL_FUNCTIONS = {
    **TOOL_FUNCTIONS, **FIREBASE_TOOL_FUNCTIONS, **GITHUB_TOOL_FUNCTIONS,
    **META_BUILDER_TOOL_FUNCTIONS, **CREATE_TOOL_FUNCTIONS, **_custom_functions,
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

If a task genuinely needs a capability none of your existing tools provide, \
use create_tool to write and permanently save a new one. Prefer existing tools \
whenever they can do the job.

If you're stuck on a bug after a few real attempts, consider whether a diagnostic \
tool would help rather than continuing to guess blindly.

For any task with 3 or more distinct steps, call write_todos first to lay \
out the plan, then update it as steps complete.

If an edit makes things worse, use revert_file to get back to the last \
known-good state before trying a different approach."""

MAX_TURNS = 40


def _execute_tool_call(tool_call, confirm_destructive=False, confirm_callback=None):
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


def run_agent(task, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True):
    """
    Single-shot task runner. Each call starts a fresh conversation.
    use_memory=False for fan-out leaves to avoid parallel writes to .agent_memory.json.
    """
    memory_context = ""
    if use_memory:
        relevant = task_memory.retrieve_relevant(task)
        memory_context = task_memory.format_for_prompt(relevant)

    system_content = SYSTEM_PROMPT
    if memory_context:
        system_content += "\n\n" + memory_context

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": task},
    ]

    final = _run_loop(messages, on_step, auto_confirm, confirm_callback)
    if use_memory:
        task_memory.add_task_summary(task, final)
    return final


class Conversation:
    """
    Stateful multi-turn session. Message history grows across turns so
    follow-up messages build on what was just discussed.
    Memory applied once at conversation start (based on the first message),
    then the growing history itself carries context.
    """

    def __init__(self, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True):
        self.on_step = on_step
        self.auto_confirm = auto_confirm
        self.confirm_callback = confirm_callback
        self.use_memory = use_memory
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._memory_applied = False
        self._first_task = None

    def send(self, task):
        if not self._memory_applied and self.use_memory:
            relevant = task_memory.retrieve_relevant(task)
            memory_context = task_memory.format_for_prompt(relevant)
            if memory_context:
                self.messages[0]["content"] += "\n\n" + memory_context
            self._memory_applied = True
            self._first_task = task

        self.messages.append({"role": "user", "content": task})
        final = _run_loop(self.messages, self.on_step, self.auto_confirm, self.confirm_callback)
        self.messages.append({"role": "assistant", "content": final})

        if self.use_memory:
            task_memory.add_task_summary(self._first_task or task, final)

        return final


def _run_loop(messages, on_step, auto_confirm, confirm_callback):
    """Shared tool-calling loop used by run_agent() and Conversation.send()."""
    consecutive_empty = 0

    for turn in range(MAX_TURNS):
        message = ask_ai(messages, tools=TOOL_SCHEMA)

        if isinstance(message, dict) and "error" in message:
            return f"ERROR: {message['error']}"

        content = message.get("content") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else message.tool_calls

        if on_step:
            on_step({"turn": turn, "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            if not content or not content.strip():
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    return "ERROR: model returned empty responses repeatedly — task did not complete."
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": "Your last response was empty. Please continue."})
                continue
            return content

        consecutive_empty = 0
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tc_dict = tc if isinstance(tc, dict) else {
                "id": tc.id,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            result = _execute_tool_call(
                tc_dict, confirm_destructive=auto_confirm, confirm_callback=confirm_callback
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc_dict["id"],
                "content": str(result)[:6000],
            })

    return "Reached max turns without finishing — task may be too large for one run."


def fan_out(task_template, targets, max_workers=8, auto_confirm=True):
    """
    Run run_agent() in parallel across many targets.

    Example:
        fan_out("Review {target} for bugs.", ["auth.py", "payments.py", "db.py"])

    Returns {target: result}. use_memory=False on leaves to avoid
    many parallel workers writing to .agent_memory.json simultaneously.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(
                run_agent, task_template.format(target=t),
                None, auto_confirm, None, False
            ): t
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

## Tool Definitions — `tools.py`

Every built-in action the agent can take. Each function is registered in `TOOL_SCHEMA` (OpenAI-compatible) so the LLM can call it by name.

### Available Tools

| Tool | Description |
|---|---|
| `read_file` | Read a file, with optional line range to save tokens on large files |
| `write_file` | Atomic write via temp file + `os.replace()` — crash-safe |
| `edit_file` | Targeted find-and-replace; requires exactly one match |
| `read_files` | Read multiple files in one call; caps combined output at 20,000 chars |
| `list_directory` | List files and folders at a path |
| `search_codebase` | Grep-style text or regex search across all files under a directory |
| `run_bash` | Run any shell command; destructive patterns require confirmation |
| `revert_file` | `git checkout -- <path>` to undo a bad edit |
| `detect_and_run_tests` | Auto-detect and run pytest or npm test |
| `write_todos` | Create/update a live task checklist for multi-step work |
| `git_commit` | Stage all and commit; injection-safe subprocess list |

### Safety Model

**Soft blocks** — require `confirmed=True` or human approval:
```
rm -rf         git push --force    git reset --hard
drop table     mkfs                dd if=
> /dev/sd*     chmod -R 777        :(){  (fork bomb)
```

**Hard blocks** — never run, even with confirmation:
```
rm -rf /     rm -rf /*     mkfs.*     fork bomb variant
```

### Code

```python
"""
tools.py — the actions the agent can actually take.
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
    """Targeted find-and-replace. Requires exactly one match."""
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
    output = output[-4000:]
    return f"(exit code {result.returncode})\n{output}"


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


TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file's contents. Optionally give line_start/line_end.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
        }, "required": ["path"]},
    }},
    # ... (write_file, edit_file, list_directory, search_codebase,
    #      read_files, revert_file, run_bash, detect_and_run_tests,
    #      write_todos, git_commit — all follow the same pattern)
]

TOOL_FUNCTIONS = {
    "read_file": read_file,  "read_files": read_files,
    "write_file": write_file, "edit_file": edit_file,
    "revert_file": revert_file, "list_directory": list_directory,
    "search_codebase": search_codebase, "run_bash": run_bash,
    "detect_and_run_tests": detect_and_run_tests,
    "write_todos": write_todos, "git_commit": git_commit,
}
```

---

## Provider Pool — `provider_pool.py`

Manages multiple API keys across multiple providers. Rotates across slots automatically, applies cooldowns on rate limits, and falls back through a priority order.

### Priority Order

```
Cerebras (fastest)  →  OpenRouter  →  Groq (fallback)
```

### Features

| Feature | Description |
|---|---|
| Multi-key rotation | Loads `KEY`, `KEY_2`, `KEY_3` … `KEY_N` automatically per provider |
| Idle-first selection | Prefers idle + off-cooldown; falls back to off-cooldown; last resort: soonest-free |
| Rate limit cooldown | 429 → 60s cooldown; network error → 10s cooldown |
| Thread-safe | `threading.Lock()` guards all slot state mutations |
| Tool-call support | Passes `tools` + `tool_choice` to providers that support native function calling |
| Groq fallback | If all primary pool slots fail, retries through every Groq key |
| Model overrides | `CEREBRAS_MODEL`, `GROQ_MODEL`, `OPENROUTER_MODEL` env vars override defaults |
| Gemini key loaded | `GEMINI_API_KEY` loaded (used by `task_memory.py` for embeddings) |

### Default Models

| Provider | Default Model |
|---|---|
| Cerebras | `gpt-oss-120b` |
| OpenRouter | `openai/gpt-oss-120b` |
| Groq | `openai/gpt-oss-120b` |

### Code

```python
"""
provider_pool.py — multi-key LLM rotation with native tool-calling support.
"""

import os
import time
import threading
import requests
import json


def _load_keys(env_prefix):
    """Load KEY, KEY_2, KEY_3, ... until one is missing."""
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


def _build_pool():
    pool = [{"provider": "cerebras", "key": k, "model": CEREBRAS_MODEL} for k in CEREBRAS_KEYS]
    pool += [{"provider": "openrouter", "key": k, "model": OPENROUTER_MODEL} for k in OPENROUTER_KEYS]
    return pool


PROVIDER_POOL = _build_pool()
_key_lock = threading.Lock()
_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(PROVIDER_POOL))}
_groq_key_lock = threading.Lock()
_groq_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(GROQ_KEYS))}


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


def _mark_rate_limited(state, lock, index, cooldown_seconds=60):
    with lock:
        state[index]["cooldown_until"] = time.time() + cooldown_seconds


def ask_ai(messages, tools=None, tool_choice="auto", max_tokens=4096):
    """
    Rotate across provider pool.
    Returns the raw message object, or {"error": "..."} on total failure.
    """
    if not PROVIDER_POOL and not GROQ_KEYS:
        return {"error": "No API keys found in environment."}

    last_error = "unknown failure"

    for _ in range(len(PROVIDER_POOL)):
        idx = _pick_available_index(_key_state, _key_lock, len(PROVIDER_POOL))
        slot = PROVIDER_POOL[idx]
        try:
            url = (
                "https://api.cerebras.ai/v1/chat/completions"
                if slot["provider"] == "cerebras"
                else "https://openrouter.ai/api/v1/chat/completions"
            )
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
                _mark_rate_limited(_key_state, _key_lock, idx, cooldown_seconds=10)
                last_error = f"could not reach {slot['provider']} ({e})"
                continue

            if resp.status_code == 429:
                _mark_rate_limited(_key_state, _key_lock, idx)
                last_error = f"rate limited on {slot['provider']} slot #{idx + 1}"
                continue

            try:
                data = resp.json()
            except Exception:
                _mark_rate_limited(_key_state, _key_lock, idx, cooldown_seconds=10)
                last_error = f"could not parse {slot['provider']} response"
                continue

            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    _mark_rate_limited(_key_state, _key_lock, idx)
                    last_error = f"{slot['provider']} slot #{idx + 1}: {data}"
                    continue
                return {"error": f"API error from {slot['provider']}: {data}"}

            return data["choices"][0]["message"]

        finally:
            with _key_lock:
                _key_state[idx]["in_use"] = False

    # Groq fallback tier
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
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown_seconds=10)
                last_error = f"could not reach Groq ({e})"
                continue

            if resp.status_code == 429:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx)
                last_error = f"rate limited on Groq key #{idx + 1}"
                continue

            try:
                data = resp.json()
            except Exception:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown_seconds=10)
                last_error = "could not parse Groq response"
                continue

            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    _mark_rate_limited(_groq_key_state, _groq_key_lock, idx)
                    last_error = f"Groq key #{idx + 1}: {data}"
                    continue
                return {"error": f"Groq error: {data}"}

            return data["choices"][0]["message"]

        finally:
            with _groq_key_lock:
                _groq_key_state[idx]["in_use"] = False

    return {"error": last_error}
```

---

## Task Memory — `task_memory.py`

Persistent cross-session memory. Every completed task gets a summary saved to `.agent_memory.json`. On a new task, the most relevant past summaries are retrieved and injected into the system prompt.

### Features

| Feature | Description |
|---|---|
| Semantic retrieval | Uses Gemini embeddings + cosine similarity when `GEMINI_API_KEY` is set |
| Keyword fallback | Falls back to word-overlap scoring if Gemini is unavailable |
| Tiered ranking | Embedding-scored entries ranked above keyword-scored entries |
| Similarity floor | Only returns entries with cosine similarity > 0.55 to avoid irrelevant noise |
| Bounded storage | Caps at 200 entries; oldest entries evicted when limit is hit |
| Crash-safe writes | Uses temp file + `os.replace()` for atomic saves |
| Prompt formatting | `format_for_prompt()` produces a clean block ready to append to the system prompt |

### Code

```python
"""
task_memory.py — persistent memory across sessions, per project.
"""

import json
import math
import os
import time
import requests

MEMORY_FILE = ".agent_memory.json"
MAX_STORED = 200
MAX_RETRIEVED = 5

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GEMINI_EMBED_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_EMBED_MODEL}:embedContent?key={{key}}"
)


def _embed(text):
    """Returns an embedding vector, or None if unavailable/failed."""
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


def _load(memory_path):
    if not os.path.exists(memory_path):
        return []
    try:
        with open(memory_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(memory_path, entries):
    tmp = memory_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries[-MAX_STORED:], f, indent=2)
    os.replace(tmp, memory_path)


def add_task_summary(task, summary, memory_path=MEMORY_FILE):
    """Append a completed task + its outcome to persistent memory."""
    entries = _load(memory_path)
    entries.append({
        "task": task,
        "summary": summary,
        "timestamp": time.time(),
        "embedding": _embed(task),
    })
    _save(memory_path, entries)


def retrieve_relevant(task, memory_path=MEMORY_FILE, top_k=MAX_RETRIEVED):
    """
    Return the most relevant past entries.
    Tier 1: cosine similarity > 0.55 on real embeddings.
    Tier 0: keyword-overlap fallback.
    """
    entries = _load(memory_path)
    if not entries:
        return []

    query_embedding = _embed(task)
    task_words = set(task.lower().split())

    def score(entry):
        if query_embedding and entry.get("embedding"):
            sim = _cosine_similarity(query_embedding, entry["embedding"])
            return (1, sim, entry["timestamp"])
        entry_words = set(entry["task"].lower().split())
        overlap = len(task_words & entry_words)
        return (0, overlap, entry["timestamp"])

    ranked = sorted(entries, key=score, reverse=True)

    if query_embedding:
        relevant = [
            e for e in ranked
            if e.get("embedding") and _cosine_similarity(query_embedding, e["embedding"]) > 0.55
        ]
        if relevant:
            return relevant[:top_k]

    relevant = [e for e in ranked if len(task_words & set(e["task"].lower().split())) > 0]
    return relevant[:top_k]


def format_for_prompt(entries):
    if not entries:
        return ""
    lines = ["Relevant memory from past sessions in this project:"]
    for e in entries:
        lines.append(f"- Task: \"{e['task']}\" -> {e['summary']}")
    return "\n".join(lines)
```

---

## Custom Tool Registry — `custom_tool_registry.py`

Lets the agent create its own new tools at runtime, validate them, persist them, and hot-reload them immediately.

### Features

| Feature | Description |
|---|---|
| AST validation | Code is parsed and checked for syntax errors before anything is saved |
| Callable check | Code is executed in an isolated namespace to confirm the function is callable |
| Import allowlist | Only pure-stdlib modules allowed (`re`, `json`, `math`, `datetime`, etc.) — no `os`, `subprocess`, `socket` |
| Atomic schema save | `custom_tools_schema.json` written via temp file + `os.replace()` |
| Overwrite support | Re-creating a tool with the same name replaces the old one |
| Persistent across sessions | Both `custom_tools.py` and `custom_tools_schema.json` survive restarts |
| Hot reload | `_refresh_custom_tools()` in `agent.py` updates the live tool registry immediately after `create_tool` succeeds |
| Load errors isolated | A broken tool skips gracefully; other tools still load |

### Allowed Imports for Self-Created Tools

```
re  json  math  time  datetime  collections  itertools  functools
string  textwrap  difflib  random  statistics  typing  dataclasses
enum  decimal  fractions
```

### Code

```python
"""
custom_tool_registry.py — lets the agent create its OWN new tools.
"""

import ast
import importlib
import json
import os
import re

CUSTOM_TOOLS_FILE = "custom_tools.py"
CUSTOM_SCHEMA_FILE = "custom_tools_schema.json"

_VALID_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_ALLOWED_IMPORTS = {
    "re", "json", "math", "time", "datetime", "collections", "itertools",
    "functools", "string", "textwrap", "difflib", "random", "statistics",
    "typing", "dataclasses", "enum", "decimal", "fractions",
}


def _check_import_safety(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    return (f"Import of '{alias.name}' is not allowed in a self-created tool "
                            f"(allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}).")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                return f"Import from '{node.module}' is not allowed."
    return None


def _validate_code_defines_callable(name, code):
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Code has a syntax error: {e}"

    defines_target = any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in ast.walk(tree)
    )
    if not defines_target:
        return False, f"Code does not define a function named '{name}'."

    import_error = _check_import_safety(tree)
    if import_error:
        return False, import_error

    namespace = {}
    try:
        exec(compile(tree, "<custom_tool>", "exec"), namespace)
    except Exception as e:
        return False, f"Code raised an error when defining it: {e}"

    if name not in namespace or not callable(namespace[name]):
        return False, f"After execution, '{name}' is not a callable in the namespace."

    return True, None


def create_tool(name, description, parameters_json, code):
    """Validate and permanently save a new agent-created tool."""
    if not _VALID_NAME.match(name):
        return f"ERROR: '{name}' is not a valid tool name (use lowercase snake_case)."

    try:
        parameters = json.loads(parameters_json)
    except json.JSONDecodeError as e:
        return f"ERROR: parameters_json is not valid JSON: {e}"

    ok, err = _validate_code_defines_callable(name, code)
    if not ok:
        return f"ERROR: tool not saved — {err}"

    _ensure_files()
    schema_list = _load_schema()
    schema_list = [s for s in schema_list if s["function"]["name"] != name]
    schema_list.append({
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    })
    _save_schema(schema_list)

    with open(CUSTOM_TOOLS_FILE, "a") as f:
        f.write(f"\n\n# --- {name} ---\n{code}\n")

    return f"Tool '{name}' created and saved permanently. Available immediately and in all future sessions."


def load_custom_tools():
    """Load all previously created tools. Returns (schema_list, functions_dict, errors_list)."""
    _ensure_files()
    schema_list = _load_schema()
    functions = {}
    errors = []

    try:
        if "custom_tools" in importlib.sys.modules:
            module = importlib.reload(importlib.sys.modules["custom_tools"])
        else:
            module = importlib.import_module("custom_tools")
    except Exception as e:
        return [], {}, [f"Could not load custom_tools.py at all: {e}"]

    valid_schema = []
    for entry in schema_list:
        fn_name = entry["function"]["name"]
        fn = getattr(module, fn_name, None)
        if fn is None or not callable(fn):
            errors.append(f"Tool '{fn_name}' is in schema but missing/broken — skipped.")
            continue
        functions[fn_name] = fn
        valid_schema.append(entry)

    return valid_schema, functions, errors


CREATE_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "create_tool",
        "description": "Create and PERMANENTLY save a new tool when no existing tool covers "
                        "what the current task needs. Available immediately and in all future sessions. "
                        "Only use when genuinely no combination of existing tools can do the job.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "lowercase snake_case function name"},
            "description": {"type": "string"},
            "parameters_json": {"type": "string", "description": "OpenAI function-parameters schema as JSON string"},
            "code": {"type": "string", "description": "full Python function definition"},
        }, "required": ["name", "description", "parameters_json", "code"]},
    }},
]
CREATE_TOOL_FUNCTIONS = {"create_tool": create_tool}
```

---

## Custom Tools — `custom_tools.py`

Auto-generated by the agent at runtime via `create_tool`. Do not edit manually — the registry manages this file. Tools accumulate here across sessions.

### Currently Saved Tools

| Tool | Description |
|---|---|
| `count_python_lines` | Returns the number of lines in a given `.py` file |

### Code

```python
"""custom_tools.py — tools the agent has created for itself over time."""


# --- count_python_lines ---
def count_python_lines(file_path: str) -> str:
    """Return the number of lines in the given Python file."""
    import os
    if not os.path.exists(file_path):
        return f"ERROR: {file_path} does not exist."
    if not file_path.lower().endswith('.py'):
        return f"ERROR: {file_path} is not a Python file."
    try:
        with open(file_path, 'r', errors='ignore') as f:
            lines = f.readlines()
        return f"{len(lines)}"
    except Exception as e:
        return f"ERROR: Could not read file: {e}"
```

---

## Firebase Tools — `firebase_tools.py`

Generates a complete set of Firebase starter files for any web app that needs user login and per-user data.

### Features

| Feature | Description |
|---|---|
| Config scaffold | `firebase-config.js` with placeholder values and import setup |
| Auth wiring | `auth.js` — signup, login, logout, `onAuthStateChanged` listener |
| Firestore helpers | `db.js` — create, read, update, delete, owner-filtered query |
| Security rules | `firestore.rules` — auth-required reads, owner-only writes |
| Parametric | `collection_name` and `owner_field` are configurable |
| Manual step guidance | Returns a checklist of what must still be done in the Firebase console |
| Crash-safe writes | Reuses `write_file()` from `tools.py` |

### Generated Files

```
firebase-config.js    # Paste your project config here
auth.js               # signUp / logIn / logOut / watchAuthState
db.js                 # CRUD helpers for your collection
firestore.rules       # Security rules (auth required, owner-only writes)
```

### Code

```python
"""
firebase_tools.py — generates Firebase-backed app boilerplate.
"""

from tools import write_file


FIREBASE_CONFIG_TEMPLATE = """// firebase-config.js
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "YOUR_API_KEY",
  authDomain: "YOUR_PROJECT.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT.appspot.com",
  messagingSenderId: "YOUR_SENDER_ID",
  appId: "YOUR_APP_ID"
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);
"""

AUTH_TEMPLATE = """// auth.js — email/password signup, login, logout
import { createUserWithEmailAndPassword, signInWithEmailAndPassword,
         signOut, onAuthStateChanged } from "...firebase-auth.js";
import { auth } from "./firebase-config.js";

export async function signUp(email, password) { ... }
export async function logIn(email, password) { ... }
export async function logOut() { ... }
export function watchAuthState(onLoggedIn, onLoggedOut) { ... }
"""

FIRESTORE_HELPERS_TEMPLATE = """// db.js — CRUD helpers for collection: {collection_name}
export async function createDoc(id, data) { ... }
export async function getDocById(id) { ... }
export async function getAllDocs() { ... }
export async function getDocsWhereOwner(userId) { ... }
export async function updateDocById(id, data) { ... }
export async function deleteDocById(id) { ... }
"""

FIRESTORE_RULES_TEMPLATE = """rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{collection_name}/{docId} {
      allow read: if request.auth != null;
      allow create: if request.auth != null
                    && request.resource.data.{owner_field} == request.auth.uid;
      allow update, delete: if request.auth != null
                    && resource.data.{owner_field} == request.auth.uid;
    }
  }
}
"""


def scaffold_firebase_app(collection_name="items", owner_field="ownerId", output_dir="."):
    """
    Writes firebase-config.js, auth.js, db.js, and firestore.rules
    into output_dir. Returns a summary + manual steps still needed
    in the Firebase console.
    """
    files_written = []
    for filename, template in [
        ("firebase-config.js", FIREBASE_CONFIG_TEMPLATE),
        ("auth.js", AUTH_TEMPLATE),
        ("db.js", FIRESTORE_HELPERS_TEMPLATE.format(collection_name=collection_name)),
        ("firestore.rules", FIRESTORE_RULES_TEMPLATE.format(
            collection_name=collection_name, owner_field=owner_field)),
    ]:
        path = f"{output_dir}/{filename}".replace("//", "/")
        write_file(path, template)
        files_written.append(path)

    return (
        "Files generated:\n  " + "\n  ".join(files_written) + "\n\n"
        "Manual steps still needed in the Firebase console:\n"
        "  1. Create a project and add a Web app.\n"
        "  2. Copy the config values into firebase-config.js.\n"
        "  3. Enable Email/Password auth.\n"
        "  4. Create a Firestore database.\n"
        "  5. Paste firestore.rules into the Rules tab and Publish."
    )


FIREBASE_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "scaffold_firebase_app",
        "description": "Generate Firebase auth + Firestore boilerplate files for a web app "
                        "that needs user login and per-user private data.",
        "parameters": {"type": "object", "properties": {
            "collection_name": {"type": "string", "default": "items"},
            "owner_field": {"type": "string", "default": "ownerId"},
            "output_dir": {"type": "string", "default": "."},
        }},
    }},
]
FIREBASE_TOOL_FUNCTIONS = {"scaffold_firebase_app": scaffold_firebase_app}
```

---

## GitHub Tools — `github_tools.py`

Real GitHub integration via the REST API. Uses `GITHUB_TOKEN` (set as a Replit Secret). Auto-detects the repo owner/name from `git remote origin`.

### Features

| Feature | Description |
|---|---|
| Auto repo detection | Parses `owner/repo` from both HTTPS and SSH remote URL formats |
| `git_push` | Push the current (or named) branch to origin |
| `create_branch` | Create and checkout a new branch off an up-to-date base |
| `open_pull_request` | Open a real PR via the GitHub API; returns the PR URL |
| `list_open_issues` | Fetch open issues for pre-work context |
| No CLI dependency | Uses `requests` directly — no `gh` binary required |

### Code

```python
"""
github_tools.py — real GitHub integration: push, branch, open PRs.
"""

import os
import re
import requests
from tools import run_bash

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"


def _get_repo_slug():
    """Parse 'owner/repo' from git remote get-url origin (HTTPS or SSH)."""
    result = run_bash("git remote get-url origin", confirmed=True)
    if "exit code 0" not in result:
        return None, f"Could not read git remote: {result}"
    url_line = result.split("\n", 1)[1].strip() if "\n" in result else ""
    match = re.search(r"github\.com[:/]([^/]+)/([^/.\s]+)", url_line)
    if not match:
        return None, f"Could not parse a GitHub owner/repo from: {url_line}"
    return f"{match.group(1)}/{match.group(2)}", None


def _headers():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"}


def git_push(branch=None, set_upstream=True):
    if not branch:
        result = run_bash("git rev-parse --abbrev-ref HEAD", confirmed=True)
        branch = result.split("\n", 1)[1].strip()
    cmd = f"git push {'--set-upstream ' if set_upstream else ''}origin {branch}"
    return run_bash(cmd, confirmed=True, timeout=60)


def create_branch(branch_name, from_branch="main"):
    return run_bash(
        f"git checkout {from_branch} && git pull && git checkout -b {branch_name}",
        confirmed=True
    )


def open_pull_request(title, body="", head=None, base="main"):
    if not GITHUB_TOKEN:
        return "ERROR: GITHUB_TOKEN not set. Add it as a Replit Secret first."
    repo_slug, err = _get_repo_slug()
    if err:
        return f"ERROR: {err}"
    if not head:
        result = run_bash("git rev-parse --abbrev-ref HEAD", confirmed=True)
        head = result.split("\n", 1)[1].strip()
    try:
        resp = requests.post(
            f"{GITHUB_API}/repos/{repo_slug}/pulls",
            headers=_headers(),
            json={"title": title, "body": body, "head": head, "base": base},
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        return f"ERROR: could not reach GitHub API: {e}"
    if resp.status_code == 201:
        return f"Pull request opened: {resp.json()['html_url']}"
    return f"ERROR: GitHub API returned {resp.status_code}: {resp.text[:500]}"


def list_open_issues(limit=10):
    if not GITHUB_TOKEN:
        return "ERROR: GITHUB_TOKEN not set."
    repo_slug, err = _get_repo_slug()
    if err:
        return f"ERROR: {err}"
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo_slug}/issues",
            headers=_headers(),
            params={"state": "open", "per_page": limit},
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        return f"ERROR: {e}"
    if resp.status_code != 200:
        return f"ERROR: GitHub API returned {resp.status_code}: {resp.text[:500]}"
    issues = resp.json()
    if not issues:
        return "No open issues."
    return "\n".join(f"#{i['number']}: {i['title']}" for i in issues if "pull_request" not in i)


GITHUB_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "git_push",
        "description": "Push the current git branch to GitHub (origin).",
        "parameters": {"type": "object", "properties": {
            "branch": {"type": "string", "description": "defaults to current branch"},
        }},
    }},
    {"type": "function", "function": {
        "name": "create_branch",
        "description": "Create and check out a new git branch.",
        "parameters": {"type": "object", "properties": {
            "branch_name": {"type": "string"},
            "from_branch": {"type": "string", "default": "main"},
        }, "required": ["branch_name"]},
    }},
    {"type": "function", "function": {
        "name": "open_pull_request",
        "description": "Open a real pull request on GitHub. Push the branch first.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "body": {"type": "string", "default": ""},
            "head": {"type": "string", "description": "defaults to current branch"},
            "base": {"type": "string", "default": "main"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "list_open_issues",
        "description": "List open GitHub issues for context before starting work.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 10},
        }},
    }},
]
GITHUB_TOOL_FUNCTIONS = {
    "git_push": git_push, "create_branch": create_branch,
    "open_pull_request": open_pull_request, "list_open_issues": list_open_issues,
}
```

---

## Meta Builder — `meta_builder.py`

Generates a complete, self-contained N-agent simulation system from a single theme prompt. No manual follow-up files needed.

### Features

| Feature | Description |
|---|---|
| Parallel persona generation | Splits target count into batches; runs batch LLM calls in parallel via `ThreadPoolExecutor` |
| Name deduplication | First batch runs alone; remaining batches are told to avoid those names |
| Partial result preservation | Failed batches are reported but successful ones are still used |
| Markdown fence stripping | Handles models that wrap JSON in fences despite instructions |
| Full system output | Writes `agents.json`, `memory.py`, `tick_engine.py`, and `viewer.py` |
| Theme-parameterised | All generated files reference the user's theme, not hardcoded examples |

### Generated Files

```
agents.json       # Full persona roster (personality, routine, relationships, secrets)
memory.py         # Per-agent memory store (recency + importance + cosine scoring)
tick_engine.py    # Hourly tick loop — builds prompt, calls LLM, logs action, stores memory
viewer.py         # Flask web server — live feed that auto-refreshes every 5s
```

### Persona Fields

Each agent in `agents.json` has:

| Field | Description |
|---|---|
| `id` | Unique lowercase snake_case identifier |
| `name` | Display name |
| `role` | Role in the simulation theme |
| `personality` | Comma-separated traits |
| `backstory` | 1–2 sentence origin |
| `routine` | `{"hour_range": "activity"}` covering the full day |
| `relationships` | `{"other_agent_name": "relationship description"}` |
| `secret` | Private goal or hidden truth that quietly drives behaviour |

### Code

```python
"""
meta_builder.py — generates a complete N-agent simulation system from one theme prompt.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from provider_pool import ask_ai

BATCH_SIZE = 5
MAX_TOKENS_PER_BATCH = 3000


def _persona_prompt(theme, n, existing_names):
    avoid = f" Do not reuse these names: {', '.join(existing_names)}." if existing_names else ""
    return (
        f"Generate exactly {n} distinct characters for an agent-based simulation "
        f"themed around: {theme}.\n\n"
        f"Each character needs: id, name, role, personality, backstory, routine "
        f"(dict of time-range strings to activities), relationships (dict), secret.{avoid}\n\n"
        f"Respond with ONLY a JSON array of {n} objects, no markdown fences, no commentary."
    )


def _extract_json_array(text):
    """Strip markdown fences before parsing."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _generate_batch(theme, n, existing_names):
    prompt = _persona_prompt(theme, n, existing_names)
    message = ask_ai([{"role": "user", "content": prompt}], max_tokens=MAX_TOKENS_PER_BATCH)
    if isinstance(message, dict) and "error" in message:
        return [], f"Batch failed: {message['error']}"
    content = message.get("content") if isinstance(message, dict) else message.content
    try:
        agents = _extract_json_array(content)
        if not isinstance(agents, list):
            return [], f"Batch did not return a JSON array: {content[:200]}"
        return agents, None
    except (json.JSONDecodeError, TypeError) as e:
        return [], f"Batch JSON parse failed ({e}): {content[:200]}"


def generate_roster(theme, count=30, batch_size=BATCH_SIZE, max_workers=6):
    """Generate count personas in parallel batches. Returns (agents_list, errors_list)."""
    batches = []
    remaining = count
    while remaining > 0:
        n = min(batch_size, remaining)
        batches.append(n)
        remaining -= n

    all_agents = []
    errors = []

    # First batch runs alone so later batches can avoid duplicate names
    first_agents, err = _generate_batch(theme, batches[0], [])
    all_agents.extend(first_agents)
    if err:
        errors.append(err)

    if len(batches) > 1:
        existing_names = [a.get("name", "") for a in all_agents]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_generate_batch, theme, n, existing_names): n
                for n in batches[1:]
            }
            for future in as_completed(futures):
                agents, err = future.result()
                all_agents.extend(agents)
                if err:
                    errors.append(err)

    return all_agents, errors


def build_agent_system(theme, count=30, output_dir="."):
    """
    Generate agents.json, memory.py, tick_engine.py, and viewer.py
    for the given theme. Returns a summary string.
    """
    agents, errors = generate_roster(theme, count)
    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, "agents.json"), "w") as f:
        json.dump({"agents": agents}, f, indent=2)

    # memory.py, tick_engine.py, and viewer.py are written from
    # MEMORY_TEMPLATE, TICK_ENGINE_TEMPLATE, VIEWER_TEMPLATE (see source)

    summary = f"Generated {len(agents)}/{count} agents for theme: '{theme}'\n"
    summary += "Fully self-contained — run `python tick_engine.py` and `python viewer.py`.\n"
    if errors:
        summary += f"\n{len(errors)} batch(es) had issues:\n" + "\n".join(f"  - {e}" for e in errors)
    return summary
```

---

## Tick Engine — `tick_engine.py`

Drives the simulation tick-by-tick. Each tick = one in-world hour. For every agent: build prompt → call LLM → log action → store memory.

### Features

| Feature | Description |
|---|---|
| Hourly ticks | Cycles through hours 0–23, then increments the day and repeats |
| Routine-aware prompts | `routine_for_hour()` picks the correct activity for the current hour, including midnight-wrapping ranges |
| Memory injection | Top 5 relevant memories retrieved per agent per tick |
| Personality + secrets | Both woven into the prompt; secret influences quietly without being stated outright |
| Relationship context | Agent's relationships listed in the prompt |
| Resilient LLM calls | A single failed API call skips that agent's turn; the simulation keeps running |
| Configurable endpoint | `LLM_BASE_URL` and `LLM_MODEL` env vars — works with any OpenAI-compatible provider |
| Output truncation | `max_tokens=200` keeps actions short and story-beat-sized |

### Code

```python
"""
tick_engine.py — the heart of the simulation. Each tick = one in-world hour.
"""

import json
import os
import time
import requests
from memory import AgentMemoryStore

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3-coder")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

TICK_HOURS = list(range(24))
EVENT_LOG_PATH = "event_log.json"
AGENTS_PATH = "agents.json"


def load_agents():
    with open(AGENTS_PATH, "r") as f:
        return json.load(f)["agents"]


def routine_for_hour(agent, hour):
    """Find which routine block covers the current hour (handles midnight-wrap ranges)."""
    routine = agent.get("routine", {})
    for time_range, activity in routine.items():
        if time_range == "variable":
            continue
        if "-" in time_range:
            start, end = int(time_range.split("-")[0]), int(time_range.split("-")[1])
            if start < end:
                if start <= hour < end:
                    return activity
            else:  # e.g. "21-6" wraps past midnight
                if hour >= start or hour < end:
                    return activity
    return routine.get("variable", "No specific plan this hour.")


def build_prompt(agent, hour, day, memories):
    memory_text = "\n".join(f"- {m.text}" for m in memories) if memories else "No notable memories yet."
    relationships = "\n".join(f"- {name}: {desc}" for name, desc in agent.get("relationships", {}).items())
    return f"""You are {agent['name']}, {agent['role']} in a small Japanese town.

Personality: {agent['personality']}
Backstory: {agent.get('backstory', '')}
Private goal/secret: {agent.get('secret', 'None')}

Current time: Day {day}, Hour {hour}:00
Your usual routine at this hour: {routine_for_hour(agent, hour)}

Relationships:
{relationships or 'No notable relationships defined.'}

Relevant memories:
{memory_text}

What do you do or say right now? Respond in 1-3 sentences, third person,
as a short story beat. Stay strictly in character."""


def call_llm(prompt: str) -> str:
    if not LLM_API_KEY:
        return f"[NO API KEY SET] Would have prompted: {prompt[:60]}..."
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200}
    try:
        resp = requests.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        return f"[API ERROR — skipped this turn] {e}"
    except (KeyError, IndexError, ValueError) as e:
        return f"[MALFORMED RESPONSE — skipped this turn] {e}"


def log_event(day, hour, agent_name, action_text):
    entry = {"day": day, "hour": hour, "agent": agent_name, "text": action_text, "logged_at": time.time()}
    log = []
    if os.path.exists(EVENT_LOG_PATH):
        with open(EVENT_LOG_PATH, "r") as f:
            log = json.load(f)
    log.append(entry)
    with open(EVENT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[Day {day} {hour:02d}:00] {agent_name}: {action_text}")


def run_tick(day, hour, agents, stores):
    global_tick = day * 24 + hour
    for agent in agents:
        store = stores[agent["id"]]
        memories = store.retrieve_relevant(current_tick=global_tick, top_k=5)
        prompt = build_prompt(agent, hour, day, memories)
        action_text = call_llm(prompt)
        log_event(day, hour, agent["name"], action_text)
        store.add(text=action_text, tick=global_tick, importance=5)


def main():
    agents = load_agents()
    stores = {a["id"]: AgentMemoryStore(a["id"]) for a in agents}
    day = 0
    while True:
        for hour in TICK_HOURS:
            run_tick(day, hour, agents, stores)
            time.sleep(1)
        day += 1


if __name__ == "__main__":
    main()
```

---

## Agent Memory — `memory.py`

Per-agent JSON-backed memory store. Used by `tick_engine.py` and generated by `meta_builder.py`.

### Features

| Feature | Description |
|---|---|
| JSON persistence | Each agent gets `.agent_memory_{id}.json` — survives restarts |
| Importance-first retrieval | Higher `importance` values surface first; recency breaks ties |
| Future-entry guard | Filters out any entries with tick > current_tick |
| Size limit | Caps at 200 entries; oldest trimmed on save |
| Crash-safe writes | temp file + `os.replace()` |
| `MemoryItem` dataclass | `.text`, `.tick`, `.importance` accessible via attribute |

### Code

```python
"""
memory.py — simple per-agent memory store used by the generated tick_engine.
"""

import json
import os
from dataclasses import dataclass
from typing import List


@dataclass
class MemoryItem:
    text: str
    tick: int
    importance: int


class AgentMemoryStore:
    MAX_STORED = 200

    def __init__(self, agent_id: str, memory_path: str | None = None):
        self.agent_id = agent_id
        self.path = memory_path or f".agent_memory_{agent_id}.json"
        self._entries: List[dict] = self._load()

    def _load(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self) -> None:
        to_save = self._entries[-self.MAX_STORED:]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
        os.replace(tmp, self.path)

    def add(self, text: str, tick: int, importance: int = 5) -> None:
        self._entries.append({"text": text, "tick": tick, "importance": importance})
        self._save()

    def retrieve_relevant(self, current_tick: int, top_k: int = 5) -> List[MemoryItem]:
        """Return top_k memories by importance desc, then recency desc."""
        candidates = [e for e in self._entries if e.get("tick", 0) <= current_tick]
        candidates.sort(key=lambda e: (e.get("importance", 0), e.get("tick", 0)), reverse=True)
        selected = candidates[:top_k]
        return [MemoryItem(text=e["text"], tick=e["tick"], importance=e.get("importance", 0))
                for e in selected]
```

---

## Director — `director.py`

Inject world events or personal events into a running simulation. Every agent immediately receives the event as a max-importance memory and reacts on their next tick.

### Features

| Feature | Description |
|---|---|
| `inject_event` | Broadcasts a world event to ALL agents + logs it in the event feed |
| `inject_event_for_agent` | Targeted personal event for a single agent by `agent_id` |
| Max importance | Events are stored with `importance=10` (maximum) so they always surface on the next tick |
| Tick inference | Auto-detects current simulation time from `event_log.json` |
| Viewer tagging | World events logged as `"agent": "WORLD EVENT"` for distinct styling in `viewer.py` |
| Works on any system | Only depends on `agents.json` and `memory.py` — no theme-specific code |

### Usage

```python
# While tick_engine.py is running or paused:
from director import inject_event, inject_event_for_agent

inject_event("A mass casualty event just arrived — a bus crash with 12 injured.")
inject_event_for_agent("nurse_keiko", "You just received a call from your daughter.")
```

### Code

```python
"""
director.py — inject events into a running simulation.
"""

import json
import os
import time
from memory import AgentMemoryStore

AGENTS_PATH = "agents.json"
EVENT_LOG_PATH = "event_log.json"
EVENT_IMPORTANCE = 10


def _load_agents():
    with open(AGENTS_PATH, "r") as f:
        return json.load(f)["agents"]


def _current_tick():
    """Infer current tick from last entry in event_log.json."""
    if not os.path.exists(EVENT_LOG_PATH):
        return 0
    with open(EVENT_LOG_PATH, "r") as f:
        log = json.load(f)
    if not log:
        return 0
    last = log[-1]
    return last.get("day", 0) * 24 + last.get("hour", 0)


def inject_event(event_text, tick=None):
    """
    Broadcast a world event to all agents.
    - Logged to event_log.json (shows in viewer.py with WORLD EVENT tag)
    - Added as importance=10 memory to every agent
    Returns how many agents received the event.
    """
    agents = _load_agents()
    current_tick = tick if tick is not None else _current_tick()

    for agent in agents:
        store = AgentMemoryStore(agent["id"])
        store.add(text=f"[WORLD EVENT] {event_text}", tick=current_tick, importance=EVENT_IMPORTANCE)

    log = []
    if os.path.exists(EVENT_LOG_PATH):
        with open(EVENT_LOG_PATH, "r") as f:
            log = json.load(f)
    day, hour = divmod(current_tick, 24)
    log.append({"day": day, "hour": hour, "agent": "WORLD EVENT",
                "text": event_text, "logged_at": time.time()})
    with open(EVENT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)

    return f"Event injected at Day {day} {hour:02d}:00, delivered to {len(agents)} agents."


def inject_event_for_agent(agent_id, event_text, tick=None):
    """Deliver a personal event to a single agent."""
    agents = _load_agents()
    if not any(a["id"] == agent_id for a in agents):
        return f"ERROR: no agent with id '{agent_id}' found."
    current_tick = tick if tick is not None else _current_tick()
    store = AgentMemoryStore(agent_id)
    store.add(text=f"[PERSONAL EVENT] {event_text}", tick=current_tick, importance=EVENT_IMPORTANCE)
    return f"Personal event delivered to '{agent_id}' at tick {current_tick}."
```

---

## Environment Variables & API Keys

| Variable | Required | Used By | Description |
|---|---|---|---|
| `CEREBRAS_API_KEY` | Recommended | `provider_pool.py` | Primary (fastest) LLM provider |
| `CEREBRAS_API_KEY_2` … `_N` | Optional | `provider_pool.py` | Additional Cerebras keys for rotation |
| `OPENROUTER_API_KEY` | Optional | `provider_pool.py` | Secondary LLM provider |
| `OPENROUTER_API_KEY_2` … `_3` | Optional | `provider_pool.py` | Additional OpenRouter keys |
| `GROQ_API_KEY` | Optional | `provider_pool.py` | Fallback LLM provider |
| `GEMINI_API_KEY` | Optional | `task_memory.py` | Enables semantic (embedding-based) memory retrieval |
| `GITHUB_TOKEN` | Optional | `github_tools.py` | Required for `open_pull_request` and issue listing |
| `LLM_API_KEY` | Optional | `tick_engine.py` | API key for the simulation tick engine |
| `LLM_BASE_URL` | Optional | `tick_engine.py` | Override tick engine endpoint (default: OpenRouter) |
| `LLM_MODEL` | Optional | `tick_engine.py` | Override tick engine model (default: `qwen/qwen3-coder`) |
| `CEREBRAS_MODEL` | Optional | `provider_pool.py` | Override default Cerebras model |
| `GROQ_MODEL` | Optional | `provider_pool.py` | Override default Groq model |
| `OPENROUTER_MODEL` | Optional | `provider_pool.py` | Override default OpenRouter model |

At least one of `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, or `GROQ_API_KEY` must be set.

---

## How Everything Connects

```
cli.py
  │
  ├─ one-shot ──► run_agent(task)
  │                   │
  └─ interactive ──► Conversation.send(task)
                          │
                     agent.py: _run_loop()
                          │
                     ┌────▼────────────────────────────────┐
                     │            ask_ai()                  │
                     │  provider_pool.py                    │
                     │  Cerebras → OpenRouter → Groq        │
                     └────┬────────────────────────────────┘
                          │ tool_calls
                     _execute_tool_call()
                          │
                     ┌────▼──────────────────────────────────────────┐
                     │ tools.py       read/write/edit/run/git/todos   │
                     │ firebase_tools scaffold_firebase_app           │
                     │ github_tools   push/branch/PR/issues           │
                     │ meta_builder   build_agent_system              │
                     │ custom_tools   agent-created tools             │
                     │ create_tool    agent creates new tools         │
                     └────┬──────────────────────────────────────────┘
                          │ result → back into messages → next LLM turn
                          │
                     (repeat until plain-text reply or MAX_TURNS=40)
                          │
                     task_memory.add_task_summary()
                     (.agent_memory.json  ←  Gemini embeddings or keyword fallback)


Simulation subsystem (independent, run separately):
  meta_builder.py ──► agents.json + memory.py + tick_engine.py + viewer.py
  tick_engine.py  ──► per-tick: build_prompt → call_llm → log_event → memory.add()
  director.py     ──► inject_event / inject_event_for_agent → memory.add(importance=10)
  viewer.py       ──► Flask server, reads event_log.json, auto-refreshes every 5s


Fan-out (for multi-target tasks):
  fan_out("Review {target} for bugs.", ["auth.py", "db.py", "api.py"])
      └─ ThreadPoolExecutor (up to 8 workers)
             └─ run_agent() per target (use_memory=False to avoid write conflicts)
             └─ results merged into {target: result} dict
```
