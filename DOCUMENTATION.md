# Coding Agent — Full Documentation

A self-directing coding agent that reads, writes, edits, and runs code via LLM tool-calling. It rotates across multiple API providers, maintains persistent memory across sessions, supports parallel fan-out for multi-file tasks, and can create its own new tools at runtime.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Entry Point — `cli.py`](#entry-point--clipy)
3. [Core Agent Loop — `agent.py`](#core-agent-loop--agentpy)
4. [Tool Definitions — `tools.py`](#tool-definitions--toolspy)
5. [Provider Pool — `provider_pool.py`](#provider-pool--provider_poolpy)
6. [Environment Variables & API Keys](#environment-variables--api-keys)
7. [How Everything Connects](#how-everything-connects)

---

## Project Structure

```
.
├── cli.py             # User-facing entry point (interactive + one-shot modes)
├── agent.py           # Core reasoning loop, fan-out, Conversation class
├── tools.py           # All tool implementations + OpenAI-compatible schema
├── provider_pool.py   # Multi-key LLM rotation (Cerebras → OpenRouter → Groq)
└── requirements.txt   # Python dependencies
```

---

## Entry Point — `cli.py`

The installable CLI. Supports two modes of operation.

### Usage

```bash
# Interactive chat loop (full conversation memory across turns)
python cli.py

# One-shot task (runs once, no follow-up context)
python cli.py "fix the bug in auth.py"
```

### Features

| Feature | Description |
|---|---|
| Interactive mode | Full `Conversation` object with real message history across turns |
| One-shot mode | `run_agent()` call, exits after the task completes |
| Step rendering | Streams each agent turn to the terminal live |
| Todo display | `write_todos` calls are rendered as a formatted checklist |
| Destructive confirmation | Pauses and asks the user before running any destructive shell command |
| `new` command | Resets the conversation without restarting the process |

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
    print("Type your task, or 'quit' to exit.\n")

    if len(sys.argv) > 1:
        # One-shot mode — no follow-up needed, run_agent is fine as-is.
        task = " ".join(sys.argv[1:])
        result = run_agent(task, on_step=_print_step, confirm_callback=_ask_confirmation)
        print(f"\n✅ {result}")
        return

    # Interactive mode — real conversation memory across turns.
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

The brain. Sends the conversation + tool schema to the LLM, executes returned tool calls, feeds results back in, and repeats until the model replies with plain text (task done) or `MAX_TURNS` is hit.

### Features

| Feature | Description |
|---|---|
| Tool-calling loop | Iterates up to 40 turns executing any tool calls the model requests |
| `run_agent()` | Single-shot task runner; fresh conversation each call |
| `Conversation` class | Stateful multi-turn session with real message history |
| Fan-out | Parallel `run_agent()` calls across multiple targets via `ThreadPoolExecutor` |
| Task memory | Retrieves relevant past-session summaries and saves a summary when done |
| Custom tool loading | Loads tools the agent created in past sessions at startup |
| Live tool refresh | After `create_tool` runs, new tool is usable in the same session immediately |
| Empty-response guard | Detects truncated/stalled responses and nudges the model to continue |
| Meta-builder tool | `build_agent_system` generates full N-agent simulation systems |

### System Prompt

The system prompt instructs the agent to:
- Investigate before changing anything
- Make the smallest correct change
- Run tests before declaring done
- Use `write_todos` for tasks with 3+ steps
- Use `revert_file` when an edit makes things worse
- Check project root before searching subfolders
- Create new tools only when no existing tool can do the job

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

# Load tools the agent has created for itself in past sessions
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
and shell via tools..."""  # (full prompt in source)

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
    """Single-shot task runner. Starts a fresh conversation each call."""
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
    """Stateful multi-turn session. Message history grows across turns."""

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
    """Shared tool-calling loop used by both run_agent() and Conversation.send()."""
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
                    return "ERROR: model returned empty responses repeatedly."
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
    Returns {target: result} for all targets.
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

Every action the agent can take. Each function is registered in `TOOL_SCHEMA` (OpenAI-compatible) so the LLM can call it by name.

### Available Tools

| Tool | Description |
|---|---|
| `read_file` | Read a file, with optional line range to save tokens on large files |
| `write_file` | Atomic write via temp file + `os.replace()` — crash-safe |
| `edit_file` | Targeted find-and-replace; requires exactly one match to prevent silent multi-edits |
| `read_files` | Read multiple files in one call; caps combined output at 20,000 chars |
| `list_directory` | List files and folders at a path |
| `search_codebase` | Grep-style text or regex search across all files under a directory |
| `run_bash` | Run any shell command; destructive patterns require confirmation; hard blocks never run |
| `revert_file` | `git checkout -- <path>` safety net to undo a bad edit |
| `detect_and_run_tests` | Auto-detect and run pytest or npm test |
| `write_todos` | Create/update a live task checklist for multi-step work |
| `git_commit` | Stage all and commit; uses subprocess list to prevent shell injection |

### Safety Model

**Soft blocks** (require `confirmed=True` or human approval via `confirm_callback`):
```
rm -rf          git push --force    git reset --hard
drop table      mkfs                dd if=
> /dev/sd*      chmod -R 777        :(){  (fork bomb)
```

**Hard blocks** (never run, even with confirmation):
```
rm -rf /        rm -rf /*           mkfs.*      fork bomb variant
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
        return "ERROR: paths must be a non-empty list of file paths."
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
        return f"ERROR: '{path}' is not tracked by git, nothing to revert to."
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
        return "ERROR: todos must be a non-empty list of {content, status} objects."
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
        "description": "Read a file's contents. Optionally give line_start/line_end (1-indexed) "
                        "to read only part of a large file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or completely overwrite a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Targeted find-and-replace. Prefer over write_file for small changes.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "list_directory",
        "description": "List files and folders at a given path.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."}
        }},
    }},
    {"type": "function", "function": {
        "name": "search_codebase",
        "description": "Search for text or regex across files under a directory.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "root": {"type": "string", "default": "."},
            "extensions": {"type": "array", "items": {"type": "string"}},
            "use_regex": {"type": "boolean", "default": False},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "read_files",
        "description": "Read multiple files in one call.",
        "parameters": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
        }, "required": ["paths"]},
    }},
    {"type": "function", "function": {
        "name": "revert_file",
        "description": "Restore a file to its last git-committed state.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "Run a shell command. Destructive commands will ask for confirmation.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "detect_and_run_tests",
        "description": "Auto-detect and run the project test suite (pytest or npm test).",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string", "default": "."}
        }},
    }},
    {"type": "function", "function": {
        "name": "write_todos",
        "description": "Create or update a visible task checklist. Use for tasks with 3+ steps.",
        "parameters": {"type": "object", "properties": {
            "todos": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "content": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                }, "required": ["content", "status"]},
            },
        }, "required": ["todos"]},
    }},
    {"type": "function", "function": {
        "name": "git_commit",
        "description": "Stage all changes and commit with a message.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"},
        }, "required": ["message"]},
    }},
]

TOOL_FUNCTIONS = {
    "read_file": read_file,
    "read_files": read_files,
    "write_file": write_file,
    "edit_file": edit_file,
    "revert_file": revert_file,
    "list_directory": list_directory,
    "search_codebase": search_codebase,
    "run_bash": run_bash,
    "detect_and_run_tests": detect_and_run_tests,
    "write_todos": write_todos,
    "git_commit": git_commit,
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
| Multi-key rotation | Loads `CEREBRAS_API_KEY`, `CEREBRAS_API_KEY_2` … `CEREBRAS_API_KEY_N` automatically |
| Idle-first selection | Prefers idle+off-cooldown slots; falls back to off-cooldown; last resort: soonest-free |
| Rate limit cooldown | 429 responses put a slot on 60s cooldown; network errors give a 10s cooldown |
| Thread-safe | `threading.Lock()` guards all slot state mutations |
| Tool-call support | Passes `tools` + `tool_choice` to providers that support native function calling |
| Provider fallback | If all primary pool slots fail, retries through every Groq key |
| Model overrides | `CEREBRAS_MODEL`, `GROQ_MODEL`, `OPENROUTER_MODEL` env vars override defaults |

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
    """Load <PREFIX>, then <PREFIX>_2, <PREFIX>_3, ... until one is missing."""
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
    """Prefer idle+off-cooldown → off-cooldown → soonest-free. Marks in_use atomically."""
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
    Rotate across provider pool, try Groq as fallback.
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
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {slot['key']}",
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=60,
                )
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

    # Fallback tier: Groq
    for _ in range(len(GROQ_KEYS)):
        idx = _pick_available_index(_groq_key_state, _groq_key_lock, len(GROQ_KEYS))
        api_key = GROQ_KEYS[idx]
        try:
            payload = {"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=60,
                )
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

## Environment Variables & API Keys

| Variable | Required | Description |
|---|---|---|
| `CEREBRAS_API_KEY` | Recommended | Primary (fastest) provider |
| `CEREBRAS_API_KEY_2` … `_N` | Optional | Additional Cerebras keys for rotation |
| `OPENROUTER_API_KEY` | Optional | Secondary provider |
| `OPENROUTER_API_KEY_2` … `_3` | Optional | Additional OpenRouter keys |
| `GROQ_API_KEY` | Optional | Fallback provider |
| `CEREBRAS_MODEL` | Optional | Override default Cerebras model |
| `GROQ_MODEL` | Optional | Override default Groq model |
| `OPENROUTER_MODEL` | Optional | Override default OpenRouter model |
| `GEMINI_API_KEY` | Optional | Gemini (loaded but not in primary pool yet) |

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
                     ┌────▼────┐
                     │ ask_ai  │  ◄── provider_pool.py
                     │ (LLM)   │      (Cerebras → OpenRouter → Groq)
                     └────┬────┘
                          │ tool_calls
                     _execute_tool_call()
                          │
                     tools.py  (read_file, write_file, run_bash, ...)
                     firebase_tools / github_tools / custom_tools
                          │
                     result → back into messages → next LLM turn
                          │
                     (repeat until plain-text reply or MAX_TURNS=40)
                          │
                     task_memory.add_task_summary()
```

**Fan-out** (`fan_out()`): for tasks with many independent targets, the root call spins up one `run_agent()` per target in a `ThreadPoolExecutor` (up to 8 concurrent), merges results, and returns a combined summary.
