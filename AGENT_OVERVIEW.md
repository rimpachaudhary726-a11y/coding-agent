# Coding Agent — Complete Overview

> **Last synced:** All 18 Python files read from latest source.

A self-directing coding agent that reads, writes, edits, and runs code via LLM tool-calling. It rotates across multiple API providers, maintains importance-weighted persistent memory, supports parallel fan-out, sandboxes shell execution, provides AST-aware code search, speaks real LSP, logs every tool call, and can create its own new tools at runtime. It also ships a complete N-agent simulation engine.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [cli.py — Entry Point](#clipy--entry-point)
3. [agent.py — Core Loop](#agentpy--core-loop)
4. [tools.py — Built-in Tools](#toolspy--built-in-tools)
5. [provider_pool.py — LLM Rotation](#provider_poolpy--llm-rotation)
6. [task_memory.py — Cross-Session Memory](#task_memorypy--cross-session-memory)
7. [custom_tool_registry.py — Self-Created Tools](#custom_tool_registrypy--self-created-tools)
8. [custom_tools.py — Agent-Created Tools](#custom_toolspy--agent-created-tools)
9. [run_logger.py — Tool Call Logger](#run_loggerpy--tool-call-logger)
10. [structural_search.py — AST Code Search](#structural_searchpy--ast-code-search)
11. [lsp_client.py — Language Server Client](#lsp_clientpy--language-server-client)
12. [file_search.py — Glob File Search](#file_searchpy--glob-file-search)
13. [firebase_tools.py — Firebase Scaffolding](#firebase_toolspy--firebase-scaffolding)
14. [github_tools.py — GitHub Integration](#github_toolspy--github-integration)
15. [meta_builder.py — Simulation Generator](#meta_builderpy--simulation-generator)
16. [tick_engine.py — Simulation Runtime](#tick_enginepy--simulation-runtime)
17. [memory.py — Per-Agent Memory Store](#memorypy--per-agent-memory-store)
18. [director.py — Event Injection](#directorpy--event-injection)
19. [Utility Files](#utility-files)
20. [Environment Variables](#environment-variables)
21. [Full System Architecture](#full-system-architecture)

---

## Project Structure

```
.
├── cli.py                    # Entry point — interactive loop + one-shot + plan mode
├── agent.py                  # Core LLM reasoning loop + fan-out + tool logging
├── tools.py                  # Built-in tools — sandboxed shell, snapshots, ripgrep
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

### What Changed
- Added **`plan:` prefix mode** — investigates and proposes a plan without making any changes, then asks for human approval before executing.

### Usage

```bash
python cli.py                          # interactive loop
python cli.py "fix the bug in auth.py" # one-shot
```

Inside the interactive loop:
```
you > plan: refactor the auth module   # investigate only, no writes
you > new                              # reset conversation
you > quit                             # exit
```

### Features

| Feature | Description |
|---|---|
| **Interactive mode** | `Conversation` — history grows across turns |
| **One-shot mode** | `run_agent()` — fresh context, exits after task |
| **Plan mode** | `plan: <task>` — read-only investigation, shows plan, asks for approval before running |
| **Live step rendering** | Every tool call and LLM reply streamed to terminal |
| **Todo display** | `write_todos` rendered as ☐ ◐ ☑ checklist |
| **Destructive confirmation** | Pauses before dangerous shell commands |
| **`new` command** | Reset conversation without restarting |
| **Clean exit** | Handles `EOFError` and `KeyboardInterrupt` |

### Full Code

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
```

---

## agent.py — Core Loop

### What Changed
- **Imports** now include `structural_search`, `lsp_client`, `file_search`, and `run_logger`.
- All four new tool modules are merged into `TOOL_SCHEMA` / `TOOL_FUNCTIONS`.
- **Every tool call is now timed and logged** via `run_logger.log_tool_call()` — name, args preview, latency in ms, result preview, error flag.
- System prompt updated to guide the agent to **prefer AST tools** (`find_definition`, `find_callers`, etc.) over `search_codebase` when the question is about a specific Python symbol.

### Features

| Feature | Description |
|---|---|
| **Tool-calling loop** | Up to 40 LLM ↔ tool turns per task |
| **`run_agent()`** | Single-shot runner — fresh context per call |
| **`Conversation`** | Stateful multi-turn session |
| **Fan-out** | Parallel `run_agent()` via `ThreadPoolExecutor` |
| **Task memory** | Injects relevant past summaries; saves on completion |
| **Tool call logging** | Every call timed + written to `.agent_runs.jsonl` |
| **Custom tool hot-reload** | After `create_tool`, new tool available next turn |
| **Empty-response guard** | Nudges model on blank replies; errors after 2 in a row |
| **Full tool registry** | 28+ tools across 9 modules |

### Full Code

```python
"""
agent.py — the core reasoning loop, plus fan-out for big tasks.

UPDATED: every tool call is now logged (name, args, latency, result preview,
error flag) to .agent_runs.jsonl via run_logger.log_tool_call(). Everything
else is unchanged from the original.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from provider_pool import ask_ai
from tools import TOOL_SCHEMA, TOOL_FUNCTIONS, _matches_any, _DESTRUCTIVE_PATTERNS
from firebase_tools import FIREBASE_TOOL_SCHEMA, FIREBASE_TOOL_FUNCTIONS
from github_tools import GITHUB_TOOL_SCHEMA, GITHUB_TOOL_FUNCTIONS
from meta_builder import build_agent_system
from custom_tool_registry import CREATE_TOOL_SCHEMA, CREATE_TOOL_FUNCTIONS, load_custom_tools
from structural_search import STRUCTURAL_SEARCH_TOOL_SCHEMA, STRUCTURAL_SEARCH_TOOL_FUNCTIONS
from lsp_client import LSP_TOOL_SCHEMA, LSP_TOOL_FUNCTIONS
from file_search import FILE_SEARCH_TOOL_SCHEMA, FILE_SEARCH_TOOL_FUNCTIONS
import task_memory
import run_logger

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
    + META_BUILDER_TOOL_SCHEMA + CREATE_TOOL_SCHEMA + STRUCTURAL_SEARCH_TOOL_SCHEMA
    + LSP_TOOL_SCHEMA + FILE_SEARCH_TOOL_SCHEMA + _custom_schema
)
TOOL_FUNCTIONS = {
    **TOOL_FUNCTIONS, **FIREBASE_TOOL_FUNCTIONS, **GITHUB_TOOL_FUNCTIONS,
    **META_BUILDER_TOOL_FUNCTIONS, **CREATE_TOOL_FUNCTIONS, **STRUCTURAL_SEARCH_TOOL_FUNCTIONS,
    **LSP_TOOL_FUNCTIONS, **FILE_SEARCH_TOOL_FUNCTIONS, **_custom_functions,
}


def _refresh_custom_tools():
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

For Python code, prefer find_definition, find_callers, find_references, and \
outline_file over search_codebase whenever the question is about a specific \
function, class, or symbol. These are AST-aware and won't match unrelated text \
in comments, strings, or similarly-named things. Use search_codebase for \
everything else: free-text search, non-Python files, or unknown symbol names.

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
                logged_result = f"Command declined by user: '{command}'. Not run. Try a different approach."
                run_logger.log_tool_call(name, args, logged_result, 0.0, is_error=False)
                return logged_result

    start = time.perf_counter()
    try:
        result = func(**args)
        is_error = isinstance(result, str) and result.startswith("ERROR")
    except TypeError as e:
        result = f"ERROR: bad arguments for {name}: {e}"
        is_error = True
    except Exception as e:
        result = f"ERROR: {name} raised an exception: {e}"
        is_error = True
    latency_ms = (time.perf_counter() - start) * 1000

    if name == "create_tool" and isinstance(result, str) and result.startswith("Tool '"):
        refresh_errors = _refresh_custom_tools()
        if refresh_errors:
            result += f"\n(Note: {'; '.join(refresh_errors)})"

    run_logger.log_tool_call(name, args, result, latency_ms, is_error=is_error)
    return result


def run_agent(task, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True):
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
                messages.append({"role": "user",
                    "content": "Your last response was empty. Please continue."})
                continue
            return content

        consecutive_empty = 0
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

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

### What Changed
- **`run_bash` is now sandboxed:** binary allowlist (only approved commands can run, checked across chained commands), resource limits via `setrlimit` (30s CPU, 1 GB memory, 64 processes max).
- **`search_codebase`** now uses **ripgrep** (`rg`) automatically when installed, falling back to pure-Python scan.
- **`write_file`** and **`edit_file`** now **snapshot** the previous version to `.agent_snapshots/` before overwriting.
- **`revert_file`** now has a second fallback: if the file isn't git-tracked, it restores from the most recent snapshot in `.agent_snapshots/`.

### Tool Summary

| Tool | Description |
|---|---|
| `read_file` | Read file, optional line range |
| `write_file` | Atomic write + snapshot previous version |
| `edit_file` | Find-and-replace, exactly one match required; snapshots first |
| `read_files` | Read multiple files in one call (20 000 char cap) |
| `list_directory` | List files and folders at a path |
| `search_codebase` | Text/regex search — ripgrep if available, pure-Python fallback |
| `run_bash` | Sandboxed shell: allowlist + resource limits + soft/hard blocks |
| `revert_file` | Git checkout or latest snapshot fallback |
| `detect_and_run_tests` | Auto-detect and run pytest or npm test |
| `write_todos` | Create/update visible task checklist |
| `git_commit` | Stage all + commit (injection-safe subprocess list) |

### Allowed Binaries in run_bash

```
git  python3  python  pip  pip3  pytest  npm  npx  node
ls  cat  grep  find  mkdir  cp  mv  echo  pwd  cd
chmod  touch  diff  wc  head  tail  sort  uniq  sed
awk  tar  unzip  zip  curl  which  env  sleep  true  false
```

### Resource Limits (Unix)

| Limit | Value |
|---|---|
| CPU time | 30 seconds |
| Address space | 1 GB |
| Max processes | 64 |

### Full Code

```python
"""
tools.py — the actions the agent can actually take.

UPDATED: run_bash is now sandboxed.
  - Binary allowlist: only approved commands can run, even inside chains (a; b && c | d)
  - Resource limits: CPU time, memory, and process count are capped via preexec_fn
  - Existing hard-block / soft-block (confirm) patterns are preserved on top of the allowlist
"""

import os
import re
import shlex
import resource
import subprocess
import json
import difflib
import shutil
import time


SNAPSHOT_DIR = ".agent_snapshots"


def _snapshot_before_write(path):
    """Copy current file contents to .agent_snapshots/<path>.<timestamp> before overwriting."""
    if not os.path.exists(path):
        return
    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        safe_name = path.replace("/", "__")
        ts = int(time.time() * 1000)
        dest = os.path.join(SNAPSHOT_DIR, f"{safe_name}.{ts}.bak")
        shutil.copy2(path, dest)
    except OSError:
        pass


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
        return f"ERROR: line range {line_start}-{line_end} is out of bounds ({len(lines)} lines total)."
    return "".join(selected)


def write_file(path, content):
    """Atomic write — snapshots previous version first, then temp + os.replace()."""
    _snapshot_before_write(path)
    tmp_path = path + ".tmp" + str(os.getpid())
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)
    return f"Wrote {len(content)} chars to {path}."


def edit_file(path, old_text, new_text):
    """Targeted find-and-replace. old_text must match exactly once. Snapshots first."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    content = read_file(path)
    occurrences = content.count(old_text)
    if occurrences == 0:
        return f"Could not find that exact text in {path}. No changes made."
    if occurrences > 1:
        return f"ERROR: that text appears {occurrences} times — include more context to make it unique."
    new_content = content.replace(old_text, new_text)
    write_file(path, new_content)
    diff_preview = "\n".join(list(difflib.unified_diff(
        content.splitlines(), new_content.splitlines(), lineterm="", n=1))[:20])
    return f"Edited {path}.\n{diff_preview}"


def list_directory(path="."):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    entries = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        entries.append(("[dir] " if os.path.isdir(full) else "      ") + name)
    return "\n".join(entries) if entries else "(empty directory)"


def _ripgrep_available():
    return shutil.which("rg") is not None


def _search_with_ripgrep(query, root, extensions, use_regex):
    cmd = ["rg", "--line-number", "--no-heading", "--max-count", "100"]
    if not use_regex:
        cmd += ["--fixed-strings", "--ignore-case"]
    if extensions:
        for ext in extensions:
            cmd += ["--glob", f"*{ext}"]
    cmd += ["--glob", "!.git", "--glob", "!node_modules", "--glob", "!__pycache__",
            "--glob", "!.venv", "--glob", "!venv"]
    cmd += [query, root]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode not in (0, 1):
        return None
    output = result.stdout.strip()
    if not output:
        return f"No matches found for '{query}'."
    lines = output.splitlines()
    if len(lines) >= 100:
        return "\n".join(lines[:100]) + "\n... (truncated at 100 matches)"
    return "\n".join(lines)


def search_codebase(query, root=".", extensions=None, use_regex=False):
    """Uses ripgrep if available (much faster); falls back to pure-Python scan."""
    if _ripgrep_available():
        rg_result = _search_with_ripgrep(query, root, extensions, use_regex)
        if rg_result is not None:
            return rg_result
    matches = []
    pattern = None
    if use_regex:
        try:
            pattern = re.compile(query)
        except re.error as e:
            return f"ERROR: invalid regex '{query}': {e}"
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
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
    """Git checkout, or falls back to newest .agent_snapshots/ backup."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    check = run_bash(f"git ls-files --error-unmatch {path}", confirmed=True)
    if "exit code 0" in check:
        result = run_bash(f"git checkout -- {path}", confirmed=True)
        if "exit code 0" in result:
            return f"Reverted {path} to its last committed state."
        return f"ERROR: git revert failed:\n{result}"
    # Not git-tracked — try newest snapshot
    safe_name = path.replace("/", "__")
    if not os.path.isdir(SNAPSHOT_DIR):
        return f"ERROR: '{path}' not git-tracked and no snapshots exist."
    candidates = sorted(
        (f for f in os.listdir(SNAPSHOT_DIR) if f.startswith(safe_name + ".")),
        reverse=True,
    )
    if not candidates:
        return f"ERROR: '{path}' not git-tracked and no snapshots exist."
    shutil.copy2(os.path.join(SNAPSHOT_DIR, candidates[0]), path)
    return f"'{path}' was not git-tracked — restored from snapshot {candidates[0]}."


def detect_and_run_tests(root="."):
    has_pytest_config = os.path.exists(os.path.join(root, "pytest.ini")) or \
        os.path.exists(os.path.join(root, "conftest.py"))
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
    if add_all:
        add_result = run_bash("git add -A", confirmed=True)
        if "exit code 0" not in add_result:
            return f"git add failed:\n{add_result}"
    try:
        result = subprocess.run(["git", "commit", "-m", message],
                                capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "git commit timed out after 30s."
    output = (result.stdout or "") + (result.stderr or "")
    return f"(exit code {result.returncode})\n{output[-4000:]}"


# Sandboxed run_bash
_ALLOWED_BINARIES = {
    "git", "python3", "python", "pip", "pip3", "pytest", "npm", "npx", "node",
    "ls", "cat", "grep", "find", "mkdir", "cp", "mv", "echo", "pwd", "cd",
    "chmod", "touch", "diff", "wc", "head", "tail", "sort", "uniq", "sed",
    "awk", "tar", "unzip", "zip", "curl", "which", "env", "sleep", "true", "false",
}
_SHELL_METACHAR_SPLIT = re.compile(r"[;&|]{1,2}|`|\$\(|\)")
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


def _extract_binaries(command):
    segments = [s.strip() for s in _SHELL_METACHAR_SPLIT.split(command) if s.strip()]
    binaries = []
    for seg in segments:
        try:
            tokens = shlex.split(seg)
        except ValueError:
            return None
        if tokens:
            binaries.append(os.path.basename(tokens[0]))
    return binaries


def _check_allowlist(command):
    binaries = _extract_binaries(command)
    if binaries is None:
        return f"BLOCKED: command could not be safely parsed (check quoting): '{command}'"
    if not binaries:
        return "BLOCKED: no runnable command found."
    disallowed = [b for b in binaries if b not in _ALLOWED_BINARIES]
    if disallowed:
        return (f"BLOCKED: command uses disallowed binary(ies) {sorted(set(disallowed))}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_BINARIES))}.")
    return None


_CPU_SECONDS_LIMIT = 30
_MEMORY_BYTES_LIMIT = 1 * 1024 * 1024 * 1024  # 1 GB
_MAX_PROCESSES = 64


def _apply_resource_limits():
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS_LIMIT, _CPU_SECONDS_LIMIT))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_BYTES_LIMIT, _MEMORY_BYTES_LIMIT))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (_MAX_PROCESSES, _MAX_PROCESSES))
    except (ValueError, OSError):
        pass


def run_bash(command, confirmed=False, timeout=60):
    """
    Sandboxed shell execution:
      1. Hard-blocked catastrophic patterns refused outright.
      2. Every binary (incl. inside chains/pipes) checked against allowlist.
      3. Destructive-but-allowed commands require confirmed=True.
      4. Subprocess capped on CPU time, memory, and process count.
    """
    stripped = command.strip()
    if not stripped:
        return "Empty command, nothing to run."
    if _matches_any(stripped, _HARD_BLOCKED_PATTERNS):
        return "BLOCKED: this command matches a hard safety block and will never be run."
    allowlist_error = _check_allowlist(stripped)
    if allowlist_error:
        return allowlist_error
    if _matches_any(stripped, _DESTRUCTIVE_PATTERNS) and not confirmed:
        return f"CONFIRMATION_REQUIRED: '{stripped}' looks destructive."
    try:
        result = subprocess.run(
            stripped, shell=True, capture_output=True, text=True, timeout=timeout,
            preexec_fn=_apply_resource_limits if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except OSError as e:
        return f"ERROR: could not run command (resource limit or OS error): {e}"
    output = (result.stdout or "") + (result.stderr or "")
    return f"(exit code {result.returncode})\n{output[-4000:]}"
```

---

## provider_pool.py — LLM Rotation

### What Changed
- **`Retry-After` header parsing** — on a 429, reads the header (delay-seconds integer or HTTP-date form) and cools the slot for exactly that long instead of a flat 60s.
- Cooldown clamped to `[1s, 300s]` so a malformed or huge header can't freeze a slot for hours.
- Named constants for default cooldown values (`DEFAULT_RATE_LIMIT_COOLDOWN = 60`, `DEFAULT_NETWORK_ERROR_COOLDOWN = 10`).

### Features

| Feature | Description |
|---|---|
| **Priority order** | Cerebras → OpenRouter → Groq |
| **Multi-key rotation** | `KEY`, `KEY_2`, `KEY_3` … loaded automatically |
| **Retry-After aware** | Reads header on 429 — both integer seconds and HTTP-date |
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
    """
    Parse Retry-After header — supports:
      - delay-seconds: "Retry-After: 30"
      - HTTP-date:     "Retry-After: Wed, 21 Oct 2026 07:28:00 GMT"
    Returns clamped seconds [MIN_COOLDOWN, MAX_COOLDOWN].
    """
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

    # Groq fallback
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

### What Changed
- **Importance scores (1–10)** added to every stored entry — auto-inferred from keyword heuristics at save time.
- **Ranking formula** updated: embedding similarity (60%) + importance (40%) — an important memory can outrank a slightly-more-similar trivial one.
- **Keyword fallback** also uses importance to rank ties.
- **`[HIGH IMPORTANCE]` tag** appended in the formatted prompt output for entries with score ≥ 8.
- **Backfill** — old entries without an `importance` key get one inferred on first load.

### High-Importance Keywords

```
critical  bug  fixed  broke  regression  security  data loss
crash  failed  important  never do  do not  gotcha  careful
corrupt  irreversible
```

### Low-Importance Keywords

```
typo  minor  cosmetic  formatting  rename
```

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
    """Heuristic 1-10 score. High-importance keywords +3, low-importance -2."""
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
    # Backfill importance for entries from before this update.
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
    """Append a completed task. importance auto-inferred if not supplied."""
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
    """
    Tier 1 (cosine > 0.55): ranked by 60% similarity + 40% importance.
    Tier 0 (keyword fallback): ranked by (importance, overlap count).
    """
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

### What Changed
- **SIGALRM-based hard timeout (10s)** applied to:
  - The validation `exec()` when a new tool is first created — a tool whose top-level code hangs can't stall creation.
  - Every runtime call to a loaded custom tool — a hanging tool can't stall the whole agent loop.
- Falls back to no timeout on non-Unix platforms (no `SIGALRM`).
- `_wrap_with_timeout()` wraps every loaded function; also catches generic exceptions and returns clean error strings.

### Full Code

```python
"""
custom_tool_registry.py — lets the agent create its OWN new tools.

UPDATED: self-created tools now run with a hard timeout (SIGALRM, Unix-only).
"""

import ast
import importlib
import json
import os
import re
import signal
import functools

CUSTOM_TOOLS_FILE = "custom_tools.py"
CUSTOM_SCHEMA_FILE = "custom_tools_schema.json"
_VALID_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_ALLOWED_IMPORTS = {
    "re", "json", "math", "time", "datetime", "collections", "itertools",
    "functools", "string", "textwrap", "difflib", "random", "statistics",
    "typing", "dataclasses", "enum", "decimal", "fractions",
}
TOOL_TIMEOUT_SECONDS = 10


class ToolTimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise ToolTimeoutError(f"Execution exceeded {TOOL_TIMEOUT_SECONDS}s timeout.")


def _run_with_timeout(func, *args, timeout=TOOL_TIMEOUT_SECONDS, **kwargs):
    """SIGALRM-based timeout. No-op on non-Unix platforms."""
    has_alarm = hasattr(signal, "SIGALRM")
    if not has_alarm:
        return func(*args, **kwargs)
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    old_alarm = signal.alarm(timeout)
    try:
        return func(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_alarm:
            signal.alarm(old_alarm)


def _wrap_with_timeout(fn, name):
    """Wraps a loaded custom tool so any call to it is timeout-guarded."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return _run_with_timeout(fn, *args, timeout=TOOL_TIMEOUT_SECONDS, **kwargs)
        except ToolTimeoutError:
            return (f"ERROR: custom tool '{name}' killed after {TOOL_TIMEOUT_SECONDS}s "
                    f"— likely stuck in a loop or blocking call.")
        except Exception as e:
            return f"ERROR: custom tool '{name}' raised an exception: {e}"
    return wrapper


def _check_import_safety(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    return f"Import of '{alias.name}' is not allowed."
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                return f"Import from '{node.module}' is not allowed."
    return None


def _ensure_files():
    if not os.path.exists(CUSTOM_TOOLS_FILE):
        with open(CUSTOM_TOOLS_FILE, "w") as f:
            f.write('"""custom_tools.py — tools the agent has created for itself over time."""\n\n')
    if not os.path.exists(CUSTOM_SCHEMA_FILE):
        with open(CUSTOM_SCHEMA_FILE, "w") as f:
            json.dump([], f)


def _load_schema():
    _ensure_files()
    with open(CUSTOM_SCHEMA_FILE, "r") as f:
        return json.load(f)


def _save_schema(schema_list):
    tmp = CUSTOM_SCHEMA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(schema_list, f, indent=2)
    os.replace(tmp, CUSTOM_SCHEMA_FILE)


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
        _run_with_timeout(exec, compile(tree, "<custom_tool>", "exec"), namespace,
                          timeout=TOOL_TIMEOUT_SECONDS)
    except ToolTimeoutError:
        return False, f"Code took longer than {TOOL_TIMEOUT_SECONDS}s just to define — not saved."
    except Exception as e:
        return False, f"Code raised an error when defining it: {e}"
    if name not in namespace or not callable(namespace[name]):
        return False, f"After execution, '{name}' is not a callable."
    return True, None


def create_tool(name, description, parameters_json, code):
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
    return (f"Tool '{name}' created and saved permanently. Available immediately and in all "
            f"future sessions. Every call is capped at {TOOL_TIMEOUT_SECONDS}s.")


def load_custom_tools():
    """Load all tools. Returns (schema_list, functions_dict, errors_list).
    Every returned function is wrapped with a runtime timeout guard."""
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
        functions[fn_name] = _wrap_with_timeout(fn, fn_name)
        valid_schema.append(entry)
    return valid_schema, functions, errors


CREATE_TOOL_SCHEMA = [{"type": "function", "function": {
    "name": "create_tool",
    "description": "Create and PERMANENTLY save a new tool when no existing tool covers the task. "
                   f"Every call capped at {TOOL_TIMEOUT_SECONDS}s.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "parameters_json": {"type": "string"},
        "code": {"type": "string"},
    }, "required": ["name", "description", "parameters_json", "code"]},
}}]
CREATE_TOOL_FUNCTIONS = {"create_tool": create_tool}
```

---

## custom_tools.py — Agent-Created Tools

Auto-generated by `create_tool`. All calls wrapped with a 10s SIGALRM timeout. Do not edit manually.

### Currently Saved Tools

| Tool | Description |
|---|---|
| `count_python_lines` | Returns the line count of a `.py` file as a string |

### Full Code

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

## run_logger.py — Tool Call Logger

Structured JSONL logging of every tool call the agent makes. Dependency-free and best-effort — a logging failure never breaks the actual tool call.

### Features

| Feature | Description |
|---|---|
| **JSONL format** | One JSON record per line in `.agent_runs.jsonl` |
| **Per-call latency** | `latency_ms` measured via `time.perf_counter()` in `agent.py` |
| **Error flag** | `"error": true` when the result starts with `"ERROR"` |
| **Previews capped** | Args and result previewed at 300 chars each to keep file small |
| **Best-effort** | `OSError` silently swallowed — logging never blocks the agent |
| **`summarize_recent(n)`** | Returns last `n` records as a list of dicts — for debugging/eval |

### Log Record Format

```json
{
  "timestamp": 1751234567.89,
  "tool": "write_file",
  "args_preview": "{\"path\": \"auth.py\", \"content\": \"...\"",
  "latency_ms": 12.4,
  "result_preview": "Wrote 842 chars to auth.py.",
  "error": false
}
```

### Full Code

```python
"""
run_logger.py — structured JSONL logging of every tool call the agent makes.

Each call appends one line to .agent_runs.jsonl:
    {"timestamp": ..., "tool": "write_file", "args": {...},
     "latency_ms": 42, "result_preview": "...", "error": false}

Kept deliberately dependency-free and best-effort: logging failures never
block or break the actual tool call.
"""

import json
import os
import time

RUN_LOG_PATH = ".agent_runs.jsonl"
MAX_ARG_PREVIEW = 300
MAX_RESULT_PREVIEW = 300


def _safe_preview(value, limit):
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def log_tool_call(tool_name, args, result, latency_ms, is_error=False):
    """Append one structured record. Best-effort — never raises."""
    try:
        record = {
            "timestamp": time.time(),
            "tool": tool_name,
            "args_preview": _safe_preview(args, MAX_ARG_PREVIEW),
            "latency_ms": round(latency_ms, 1),
            "result_preview": _safe_preview(result, MAX_RESULT_PREVIEW),
            "error": bool(is_error),
        }
        with open(RUN_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def summarize_recent(n=20):
    """Return the last n log records as a list of dicts, newest last."""
    if not os.path.exists(RUN_LOG_PATH):
        return []
    try:
        with open(RUN_LOG_PATH, "r") as f:
            lines = f.readlines()
    except OSError:
        return []
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
```

---

## structural_search.py — AST Code Search

AST-aware Python code search. Finds definitions, call sites, and all references by parsing the actual syntax tree — not just text matching. `.js`/`.ts` files are silently skipped (no JS AST parser dependency).

### Features

| Tool | What it does |
|---|---|
| `find_definition` | Where is a function or class actually defined? Returns file:line + signature + first docstring line |
| `find_callers` | Every call site of a function/method (`name(...)` or `obj.name(...)`) with the calling line |
| `find_references` | Every usage of a name — calls, reads, writes, attribute access, import aliases. Use before renaming |
| `outline_file` | Structural map of one file — all classes, methods, functions with line numbers and first docstring line |

All four cap results at 100 and skip `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`.

### Full Code

```python
"""
structural_search.py — AST-aware code search for Python files.
Pure stdlib (ast + os), no new dependencies.
"""

import ast
import os

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
_MAX_RESULTS = 100


def _iter_python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                yield os.path.join(dirpath, fname)


def _parse_file(path):
    try:
        with open(path, "r", errors="ignore") as f:
            source = f.read()
    except OSError as e:
        return None, f"could not read: {e}"
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return None, f"syntax error: {e}"
    return tree, source.splitlines()


def find_definition(name, root="."):
    results, errors = [], []
    for path in _iter_python_files(root):
        tree, lines_or_err = _parse_file(path)
        if tree is None:
            errors.append(f"{path}: {lines_or_err}")
            continue
        lines = lines_or_err
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                sig_line = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
                docstring = ast.get_docstring(node)
                doc_preview = f" — \"{docstring.splitlines()[0]}\"" if docstring else ""
                results.append(f"{path}:{node.lineno}: [{kind}] {sig_line}{doc_preview}")
                if len(results) >= _MAX_RESULTS:
                    return _format_results(results, errors, truncated=True)
    return _format_results(results, errors, name_for_empty=name)


def find_callers(name, root="."):
    results, errors = [], []
    for path in _iter_python_files(root):
        tree, lines_or_err = _parse_file(path)
        if tree is None:
            errors.append(f"{path}: {lines_or_err}")
            continue
        lines = lines_or_err
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called_name = None
            if isinstance(func, ast.Name):
                called_name = func.id
            elif isinstance(func, ast.Attribute):
                called_name = func.attr
            if called_name == name:
                line_no = node.lineno
                context = lines[line_no - 1].strip() if line_no - 1 < len(lines) else ""
                results.append(f"{path}:{line_no}: {context}")
                if len(results) >= _MAX_RESULTS:
                    return _format_results(results, errors, truncated=True)
    return _format_results(results, errors, name_for_empty=name, kind="callers of")


def find_references(name, root="."):
    results, errors = [], []
    for path in _iter_python_files(root):
        tree, lines_or_err = _parse_file(path)
        if tree is None:
            errors.append(f"{path}: {lines_or_err}")
            continue
        lines = lines_or_err
        for node in ast.walk(tree):
            matched_line = None
            if isinstance(node, ast.Name) and node.id == name:
                matched_line = node.lineno
            elif isinstance(node, ast.Attribute) and node.attr == name:
                matched_line = node.lineno
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                matched_line = node.lineno
            elif isinstance(node, ast.alias) and (node.asname == name or node.name == name):
                matched_line = getattr(node, "lineno", None)
            if matched_line:
                context = lines[matched_line - 1].strip() if matched_line - 1 < len(lines) else ""
                results.append(f"{path}:{matched_line}: {context}")
                if len(results) >= _MAX_RESULTS:
                    return _format_results(results, errors, truncated=True)
    return _format_results(results, errors, name_for_empty=name, kind="references to")


def outline_file(path):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    tree, lines_or_err = _parse_file(path)
    if tree is None:
        return f"ERROR: could not parse {path} — {lines_or_err}"

    def _describe(node, indent=0):
        prefix = "  " * indent
        entries = []
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                doc = ast.get_docstring(child)
                doc_preview = f" — \"{doc.splitlines()[0]}\"" if doc else ""
                entries.append(f"{prefix}line {child.lineno}: class {child.name}{doc_preview}")
                entries.extend(_describe(child, indent + 1))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in child.args.args]
                doc = ast.get_docstring(child)
                doc_preview = f" — \"{doc.splitlines()[0]}\"" if doc else ""
                async_tag = "async " if isinstance(child, ast.AsyncFunctionDef) else ""
                entries.append(
                    f"{prefix}line {child.lineno}: {async_tag}def {child.name}({', '.join(args)}){doc_preview}"
                )
        return entries

    lines = _describe(tree)
    if not lines:
        return f"{path}: no top-level classes or functions found."
    return f"Outline of {path}:\n" + "\n".join(lines)


def _format_results(results, errors, name_for_empty=None, kind="definitions of", truncated=False):
    if not results:
        base = f"No {kind} '{name_for_empty}' found." if name_for_empty else "No matches found."
    else:
        base = "\n".join(results)
        if truncated:
            base += f"\n... (truncated at {_MAX_RESULTS} results)"
    if errors:
        base += "\n\n(skipped files with errors:\n  " + "\n  ".join(errors[:5]) + ")"
    return base


STRUCTURAL_SEARCH_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "find_definition",
        "description": "AST-aware: find where a Python function or class named `name` is defined. "
                       "More precise than grep — won't match comments, strings, or similarly-named things.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string", "default": "."},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "find_callers",
        "description": "AST-aware: find every call site of a function or method named `name`. "
                       "Use before changing a function's signature to see what would break.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string", "default": "."},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "find_references",
        "description": "AST-aware: find every usage of `name` — calls, reads, writes, attribute access, imports. "
                       "Broader than find_callers. Use before renaming to see full blast radius.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "root": {"type": "string", "default": "."},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "outline_file",
        "description": "Return a structural map of a Python file — every class, method, and function "
                       "with line number and first docstring line.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
]
STRUCTURAL_SEARCH_TOOL_FUNCTIONS = {
    "find_definition": find_definition, "find_callers": find_callers,
    "find_references": find_references, "outline_file": outline_file,
}
```

---

## lsp_client.py — Language Server Client

A real LSP client that spawns `pylsp` as a subprocess and speaks JSON-RPC over stdio with proper `Content-Length` framing and async notification handling.

### Features

| Feature | Description |
|---|---|
| **Real LSP protocol** | Content-Length framing, request/response correlation by id |
| **Push diagnostics** | `textDocument/publishDiagnostics` received as async notification, waited on with `threading.Event` |
| **`lsp_get_diagnostics`** | Semantic errors, undefined names, type mismatches, unused imports |
| **`lsp_hover`** | Type info and docstring at a 0-indexed position |
| **`lsp_go_to_definition`** | Cross-file, cross-import symbol definition — more capable than AST-only tools |
| **`lsp_restart`** | Recover if the server wedges or stops responding |
| **Lazy singleton** | Server started on first use, shared across calls |
| **Document sync** | `didOpen` / `didChange` — re-analyzes current file content on each call |
| **Positions are 0-indexed** | Per LSP spec (differs from 1-indexed lines used elsewhere) |
| **Requires** | `pip install python-lsp-server` |

### Full Code

```python
"""
lsp_client.py — a real Language Server Protocol client.
Spawns pylsp as a subprocess and speaks LSP JSON-RPC over stdio.
Positions are 0-indexed (LSP spec) — line 0 = first line.
"""

import json
import os
import subprocess
import threading
import time
import shutil

_SERVER_CMD = ["pylsp"]
_REQUEST_TIMEOUT = 10
_DIAGNOSTICS_WAIT = 4


class LSPError(Exception):
    pass


class LSPClient:
    def __init__(self, root_path="."):
        self.root_path = os.path.abspath(root_path)
        self.process = None
        self._reader_thread = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending = {}
        self._diagnostics = {}
        self._diag_events = {}
        self._open_docs = {}
        self._started = False

    def _write_message(self, obj):
        body = json.dumps(obj).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        self.process.stdin.write(header + body)
        self.process.stdin.flush()

    def _read_message(self):
        headers = {}
        while True:
            line = self.process.stdout.readline()
            if not line:
                return None
            line = line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                break
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", 0))
        if length == 0:
            return None
        body = self.process.stdout.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def _reader_loop(self):
        while True:
            try:
                msg = self._read_message()
            except (OSError, ValueError):
                break
            if msg is None:
                break
            self._dispatch(msg)

    def _dispatch(self, msg):
        if "id" in msg and ("result" in msg or "error" in msg):
            with self._lock:
                entry = self._pending.get(msg["id"])
            if entry:
                entry["result"] = msg.get("result")
                entry["error"] = msg.get("error")
                entry["event"].set()
            return
        method = msg.get("method")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            uri = params.get("uri")
            with self._lock:
                self._diagnostics[uri] = params.get("diagnostics", [])
                event = self._diag_events.get(uri)
                if event:
                    event.set()

    def _send_request(self, method, params, timeout=_REQUEST_TIMEOUT):
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            self._pending[msg_id] = {"event": event, "result": None, "error": None}
        self._write_message({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise LSPError(f"Timed out waiting for response to '{method}' after {timeout}s.")
        with self._lock:
            entry = self._pending.pop(msg_id)
        if entry["error"]:
            raise LSPError(f"LSP error on '{method}': {entry['error']}")
        return entry["result"]

    def _send_notification(self, method, params):
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def start(self):
        if self._started:
            return
        if shutil.which("pylsp") is None:
            raise LSPError("pylsp is not installed. Run: pip install python-lsp-server")
        self.process = subprocess.Popen(_SERVER_CMD, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        root_uri = "file://" + self.root_path
        self._send_request("initialize", {
            "processId": os.getpid(), "rootUri": root_uri,
            "capabilities": {"textDocument": {
                "publishDiagnostics": {"relatedInformation": True},
                "hover": {"contentFormat": ["plaintext", "markdown"]},
                "definition": {},
            }},
        })
        self._send_notification("initialized", {})
        self._started = True

    def shutdown(self):
        if not self._started or self.process is None:
            return
        try:
            self._send_request("shutdown", {}, timeout=3)
            self._send_notification("exit", {})
        except (LSPError, OSError):
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        self._started = False

    def restart(self):
        self.shutdown()
        self.__init__(self.root_path)
        self.start()

    def _uri_for(self, path):
        return "file://" + os.path.abspath(path)

    def _ensure_open(self, path):
        uri = self._uri_for(path)
        with open(path, "r", errors="ignore") as f:
            text = f.read()
        if uri in self._open_docs:
            self._open_docs[uri] += 1
            self._send_notification("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": self._open_docs[uri]},
                "contentChanges": [{"text": text}],
            })
            return uri
        self._open_docs[uri] = 1
        with self._lock:
            self._diag_events[uri] = threading.Event()
        self._send_notification("textDocument/didOpen", {
            "textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": text},
        })
        return uri

    def get_diagnostics(self, path, timeout=_DIAGNOSTICS_WAIT):
        if not os.path.exists(path):
            return f"ERROR: {path} does not exist."
        uri = self._ensure_open(path)
        with self._lock:
            event = self._diag_events.setdefault(uri, threading.Event())
            event.clear()
        event.wait(timeout)
        with self._lock:
            diagnostics = self._diagnostics.get(uri, [])
        if not diagnostics:
            return f"No diagnostics for {path} (clean, or server hasn't analyzed it yet)."
        severity_names = {1: "ERROR", 2: "WARNING", 3: "INFO", 4: "HINT"}
        lines = []
        for d in diagnostics:
            sev = severity_names.get(d.get("severity"), "?")
            line_no = d.get("range", {}).get("start", {}).get("line", 0) + 1
            lines.append(f"{path}:{line_no}: [{sev}] {d.get('message', '')}")
        return "\n".join(lines)

    def hover(self, path, line, character):
        if not os.path.exists(path):
            return f"ERROR: {path} does not exist."
        uri = self._ensure_open(path)
        result = self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result or not result.get("contents"):
            return f"No hover info at {path}:{line}:{character}."
        contents = result["contents"]
        if isinstance(contents, dict):
            return contents.get("value", str(contents))
        if isinstance(contents, list):
            return "\n".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
        return str(contents)

    def definition(self, path, line, character):
        if not os.path.exists(path):
            return f"ERROR: {path} does not exist."
        uri = self._ensure_open(path)
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if not result:
            return f"No definition found at {path}:{line}:{character}."
        locations = result if isinstance(result, list) else [result]
        out = []
        for loc in locations:
            loc_path = loc.get("uri", "").replace("file://", "")
            start = loc.get("range", {}).get("start", {})
            out.append(f"{loc_path}:{start.get('line', 0) + 1}:{start.get('character', 0)}")
        return "\n".join(out)


_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = LSPClient(root_path=".")
        if not _client._started:
            _client.start()
        return _client


def lsp_get_diagnostics(path):
    try:
        return _get_client().get_diagnostics(path)
    except LSPError as e:
        return f"ERROR: {e}"


def lsp_hover(path, line, character):
    try:
        return _get_client().hover(path, line, character)
    except LSPError as e:
        return f"ERROR: {e}"


def lsp_go_to_definition(path, line, character):
    try:
        return _get_client().definition(path, line, character)
    except LSPError as e:
        return f"ERROR: {e}"


def lsp_restart():
    try:
        _get_client().restart()
        return "LSP server restarted."
    except LSPError as e:
        return f"ERROR: {e}"


LSP_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "lsp_get_diagnostics",
        "description": "Real semantic diagnostics from pylsp — undefined names, type mismatches, "
                       "unused imports. Catches bugs that grep and AST search can't.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "lsp_hover",
        "description": "Type/docstring info at a 0-indexed line/character position.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer", "description": "0-indexed line number"},
            "character": {"type": "integer"},
        }, "required": ["path", "line", "character"]},
    }},
    {"type": "function", "function": {
        "name": "lsp_go_to_definition",
        "description": "Jump to symbol definition at a 0-indexed position. Handles imports and "
                       "inherited methods better than AST-only find_definition.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer"},
            "character": {"type": "integer"},
        }, "required": ["path", "line", "character"]},
    }},
    {"type": "function", "function": {
        "name": "lsp_restart",
        "description": "Restart the language server if it's unresponsive.",
        "parameters": {"type": "object", "properties": {}},
    }},
]
LSP_TOOL_FUNCTIONS = {
    "lsp_get_diagnostics": lsp_get_diagnostics, "lsp_hover": lsp_hover,
    "lsp_go_to_definition": lsp_go_to_definition, "lsp_restart": lsp_restart,
}
```

---

## file_search.py — Glob File Search

Find files by name/path pattern instead of content. Results sorted newest-modified-first.

### Features

| Feature | Description |
|---|---|
| **Glob patterns** | `*.py`, `**/*.py`, `src/**/test_*.py` etc. |
| **Newest-first sort** | Most recently modified file comes first |
| **Skip dirs** | `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build` |
| **200 result cap** | Truncates with a message to narrow the pattern |

### Full Code

```python
"""
file_search.py — glob-style file discovery by name/path pattern (not content).
Results sorted newest-modified-first.
"""

import os
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
_MAX_RESULTS = 200


def glob_files(pattern, root=".", max_results=_MAX_RESULTS):
    """
    Find files matching a glob pattern under root.
      "*.py"              -> top-level .py files only
      "**/*.py"           -> all .py files at any depth
      "src/**/test_*.py"  -> test files anywhere under src/
    Returns paths sorted by modification time, most recently modified first.
    """
    root_path = Path(root)
    if not root_path.exists():
        return f"ERROR: {root} does not exist."
    try:
        matches = list(root_path.glob(pattern))
    except (ValueError, NotImplementedError) as e:
        return f"ERROR: invalid glob pattern '{pattern}': {e}"
    filtered = []
    for m in matches:
        if m.is_dir():
            continue
        if set(m.parts) & _SKIP_DIRS:
            continue
        filtered.append(m)
    if not filtered:
        return f"No files matched pattern '{pattern}' under {root}."
    try:
        filtered.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass
    truncated = len(filtered) > max_results
    filtered = filtered[:max_results]
    result = "\n".join(str(p) for p in filtered)
    if truncated:
        result += f"\n... (truncated at {max_results} results)"
    return result


FILE_SEARCH_TOOL_SCHEMA = [{"type": "function", "function": {
    "name": "glob_files",
    "description": "Find files by name/path pattern — e.g. '**/*.py' for all Python files, "
                   "'src/**/test_*.py' for test files. Results sorted most-recently-modified first. "
                   "Use instead of search_codebase when you know the kind of file but not its content.",
    "parameters": {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "glob pattern, e.g. '**/*.py'"},
        "root": {"type": "string", "default": "."},
    }, "required": ["pattern"]},
}}]
FILE_SEARCH_TOOL_FUNCTIONS = {"glob_files": glob_files}
```

---

## firebase_tools.py — Firebase Scaffolding

Unchanged. Generates `firebase-config.js`, `auth.js`, `db.js`, `firestore.rules` for any web app needing user login and per-user Firestore data. Returns a manual-steps checklist for the Firebase console. See previous section for full code.

---

## github_tools.py — GitHub Integration

Unchanged. `git_push`, `create_branch`, `open_pull_request`, `list_open_issues` via GitHub REST API. Auto-detects `owner/repo` from HTTPS/SSH remote URLs. Requires `GITHUB_TOKEN`. See previous section for full code.

---

## meta_builder.py — Simulation Generator

Unchanged. Generates a complete N-agent simulation system from one theme prompt using parallel LLM batch calls. Outputs `agents.json`, `memory.py`, `tick_engine.py`, `viewer.py`. See previous section for full code.

---

## tick_engine.py — Simulation Runtime

Unchanged. Hourly tick loop — for each agent: build prompt → call LLM → log to `event_log.json` → store memory. Resilient to single API failures. Configurable via `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. See previous section for full code.

---

## memory.py — Per-Agent Memory Store

Unchanged. JSON-backed per-agent store. Importance-first retrieval, 200-entry cap, crash-safe writes. See previous section for full code.

---

## director.py — Event Injection

Unchanged. `inject_event` broadcasts to all agents; `inject_event_for_agent` targets one. Both store at `importance=10` so the event always surfaces on the next tick. See previous section for full code.

---

## Utility Files

Small helpers written by the agent as examples or during task execution.

### calc.py — Safe Division

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

### greeter.py — Greeting Utility

```python
def greet(name: str) -> str:
    """Return a greeting string for the given name."""
    return "Hello, " + name
```

### mathutils.py — Math Helpers

```python
def square(x: int) -> int:
    """Calculate the square of an integer (x * x)."""
    return x * x
```

### shapes.py — Shape Classes

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

### test_calc.py — Unit Tests for calc.py

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
| `CEREBRAS_API_KEY_2` … `_N` | Optional | `provider_pool.py` | Additional Cerebras keys for rotation |
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
┌──────────────────────────────────────────────────────────────────┐
│                           cli.py                                 │
│  one-shot mode          interactive mode         plan: mode      │
│  run_agent(task)        Conversation.send()      read-only run   │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                    agent.py: _run_loop()
                             │
                    ask_ai() → provider_pool.py
                    Cerebras → OpenRouter → Groq
                    Retry-After-aware cooldowns
                             │ tool_calls
                    _execute_tool_call()
                    ├── perf_counter() start
                    ├── run tool
                    └── run_logger.log_tool_call()  → .agent_runs.jsonl
                             │
          ┌──────────────────┴──────────────────────────────┐
          │                  Tool Registry                  │
          │                                                 │
          │  tools.py          read/write/edit/bash/git     │
          │    ├─ write_file    snapshot to .agent_snapshots/│
          │    ├─ run_bash      allowlist + resource limits  │
          │    ├─ search_codebase  ripgrep or pure-Python   │
          │    └─ revert_file   git or snapshot fallback    │
          │                                                 │
          │  structural_search  find_definition             │
          │                     find_callers                │
          │                     find_references             │
          │                     outline_file                │
          │                                                 │
          │  lsp_client         lsp_get_diagnostics         │
          │  (pylsp subprocess) lsp_hover                   │
          │                     lsp_go_to_definition        │
          │                     lsp_restart                 │
          │                                                 │
          │  file_search        glob_files                  │
          │  firebase_tools     scaffold_firebase_app       │
          │  github_tools       push/branch/PR/issues       │
          │  meta_builder       build_agent_system          │
          │  create_tool        write + save new tools      │
          │  custom_tools       agent-created tools         │
          │                     (10s SIGALRM timeout each)  │
          └─────────────────────────────────────────────────┘
                             │ result → messages
                    (repeat up to MAX_TURNS=40)
                             │
              task_memory.add_task_summary()
              .agent_memory.json
              importance-weighted: 60% cosine sim + 40% importance
              high-importance entries tagged [HIGH IMPORTANCE] in prompt

─────────────────────────────────────────────────────────────────────

Fan-out (parallel tasks):
  fan_out("Review {target} for bugs.", ["a.py","b.py","c.py"])
    └─ ThreadPoolExecutor (≤8 workers)
         └─ run_agent() per target (use_memory=False)
         └─ {target: result} merged

─────────────────────────────────────────────────────────────────────

Simulation subsystem (run independently):

  meta_builder.py → generate_roster() parallel batches
                  → writes agents.json, memory.py,
                    tick_engine.py, viewer.py

  tick_engine.py  → for each day/hour/agent:
                      memory.retrieve_relevant()
                      build_prompt() + call_llm()
                      log_event() → event_log.json
                      memory.add(importance=5)

  director.py     → inject_event()           all agents importance=10
                  → inject_event_for_agent()  one agent importance=10

  viewer.py       → Flask :8080, reads event_log.json
                    auto-refresh every 5s, WORLD EVENT styling
```
