"""
tools.py — the actions the agent can actually take.

File read/write/edit logic is carried over directly from main-11.py:
- write_file() writes to a temp file then os.replace()s it, so a crash
  mid-write never leaves a corrupted file (same bug fix already proven
  out in your main project).
- edit_file() is a targeted find-and-replace, not a full rewrite — this
  is what makes precise, token-cheap patches possible instead of
  resending whole files every turn.

run_bash() is DELIBERATELY less locked-down than main-11.py's
run_command() (which only allowlists ls/pwd/date/whoami) because a
coding agent's entire job is running arbitrary build/test commands —
but every destructive-looking command still requires confirmation
before executing, and there's a hard blocklist for a few catastrophic
patterns regardless of confirmation.
"""

import os
import re
import subprocess
import json
import difflib


def read_file(path, line_start=None, line_end=None):
    """
    Reads a file. If line_start/line_end are given (1-indexed, inclusive),
    returns only that range — cheaper on large files, and matches what
    models often assume this tool can already do.
    """
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
        return f"ERROR: line range {line_start}-{line_end} is out of bounds for {path} ({len(lines)} lines total)."
    return "".join(selected)


def write_file(path, content):
    """Atomic write — same crash-safety pattern as main-11.py's write_file()."""
    tmp_path = path + ".tmp" + str(os.getpid())
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)
    return f"Wrote {len(content)} chars to {path}."


def edit_file(path, old_text, new_text):
    """Targeted find-and-replace. old_text must match EXACTLY ONCE — if it
    matches zero or multiple times, no edit is made and an error explains
    why, instead of silently rewriting every occurrence (which previously
    risked touching unrelated code that happened to share the same
    snippet)."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    content = read_file(path)
    occurrences = content.count(old_text)
    if occurrences == 0:
        return f"Could not find that exact text in {path}. No changes made. " \
               f"Tip: view the file first to copy the exact text to replace."
    if occurrences > 1:
        return f"ERROR: that text appears {occurrences} times in {path} — edit_file " \
               f"requires an exact, unique match so it never guesses which one you meant. " \
               f"No changes made. Include more surrounding context (a line above/below) " \
               f"to make old_text unique, then try again."
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
    r"""Grep-style search across text files under root. extensions e.g. [".py", ".js"].
    If use_regex is True, `query` is treated as a regular expression instead of
    a plain substring (e.g. r"def \w+\(.*\):\s*$" to find functions)."""
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


# Commands that always require explicit confirmation before running,
# regardless of the confirm flag passed in — matched against the whole
# command string, not just the first token (closes the same shell-metachar
# gap called out in main-11.py's run_command bug-fix comment).
_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-rf\b", r"\bgit\s+push\s+--force\b", r"\bgit\s+reset\s+--hard\b",
    r"\bdrop\s+table\b", r"\bmkfs\b", r"\bdd\s+if=", r">\s*/dev/sd",
    r"\bchmod\s+-R\s+777\b", r":\(\)\{",  # fork bomb pattern
]

# Absolute hard blocks — never run these even with confirmation.
_HARD_BLOCKED_PATTERNS = [
    r"\brm\s+-rf\s+/\s*$", r"\brm\s+-rf\s+/\*", r"\bmkfs\.", r":\(\)\{\s*:\|:&\s*\};:",
]


def _matches_any(command, patterns):
    return any(re.search(p, command) for p in patterns)


def run_bash(command, confirmed=False, timeout=60):
    """
    Runs a shell command. Unlike main-11.py's run_command() (which only
    allowlists 4 harmless commands), this needs to run arbitrary build/test/
    git commands — that's the whole point of a coding agent. Safety comes
    from a different angle instead: destructive-looking commands require
    confirmed=True (the CLI layer prompts the human before setting this),
    and a small set of catastrophic patterns are hard-blocked no matter what.
    """
    stripped = command.strip()
    if not stripped:
        return "Empty command, nothing to run."

    if _matches_any(stripped, _HARD_BLOCKED_PATTERNS):
        return "BLOCKED: this command matches a hard safety block and will never be run."

    if _matches_any(stripped, _DESTRUCTIVE_PATTERNS) and not confirmed:
        return (f"CONFIRMATION_REQUIRED: '{stripped}' looks destructive. "
                f"Re-run with confirmation if you're sure.")

    try:
        result = subprocess.run(
            stripped, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."

    output = (result.stdout or "") + (result.stderr or "")
    output = output[-4000:]  # cap output so huge test logs don't blow the context window
    return f"(exit code {result.returncode})\n{output}"


def read_files(paths):
    """Reads multiple files in one call instead of one tool call per file —
    saves turns on multi-file tasks. Returns each file's content labeled with
    its path; a missing/unreadable file gets an inline error instead of
    failing the whole batch."""
    if not isinstance(paths, list) or not paths:
        return "ERROR: paths must be a non-empty list of file paths."

    sections = []
    total_len = 0
    for path in paths:
        content = read_file(path)
        total_len += len(content)
        if total_len > 20000:  # cap combined size so one huge batch can't blow the context
            sections.append(f"=== {path} ===\n(skipped — combined batch size limit reached)")
            continue
        sections.append(f"=== {path} ===\n{content}")
    return "\n\n".join(sections)


def revert_file(path):
    """
    Restores a file to its last git-committed state, discarding uncommitted
    changes to it — a safety net for when an edit went wrong. Refuses if the
    path isn't tracked by git or there's nothing to revert to, rather than
    silently doing nothing or deleting a file that was never committed.
    """
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."

    check = run_bash(f"git ls-files --error-unmatch {path}", confirmed=True)
    if "exit code 0" not in check:
        return f"ERROR: '{path}' is not tracked by git, nothing to revert to. " \
               f"(Uncommitted new files can't be reverted this way — delete manually if needed.)"

    result = run_bash(f"git checkout -- {path}", confirmed=True)
    if "exit code 0" not in result:
        return f"ERROR: revert failed:\n{result}"
    return f"Reverted {path} to its last committed state."


def detect_and_run_tests(root="."):
    """
    Looks for a recognizable test setup in `root` and runs it if found.
    Checks, in order: pytest (tests/ dir or *_test.py files, or pytest.ini/
    pyproject.toml with a [tool.pytest] section), then package.json with a
    "test" script (npm test). Returns what it found and the run's output,
    or a clear message if no test setup was detected — so the caller (the
    agent) knows whether "no tests ran" means "all good" or "nothing to run".
    """
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

    return "No recognizable test setup found (no tests/, test_*.py files, pytest config, or npm test script). Nothing was run."


_VALID_STATUSES = {"pending", "in_progress", "completed"}


def write_todos(todos):
    """
    Records/updates a visible task plan for the current work — same idea as
    Claude Code's todo list. `todos` is a list of {"content": str, "status":
    "pending"|"in_progress"|"completed"}. Call this at the start of a
    multi-step task to lay out the plan, then again as steps complete to
    keep it current. Not persisted — this is a live view of THIS task,
    not permanent memory.
    """
    if not isinstance(todos, list) or not todos:
        return "ERROR: todos must be a non-empty list of {content, status} objects."

    lines = []
    for i, item in enumerate(todos):
        if not isinstance(item, dict) or "content" not in item:
            return f"ERROR: todo #{i} is missing 'content'."
        status = item.get("status", "pending")
        if status not in _VALID_STATUSES:
            return f"ERROR: todo #{i} has invalid status '{status}' (must be pending/in_progress/completed)."
        marker = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}[status]
        lines.append(f"{marker} {item['content']}")

    return "Plan updated:\n" + "\n".join(lines)


def git_commit(message, add_all=True):
    """
    Runs `git commit` directly via subprocess (no shell=True, no string
    interpolation) so a commit message containing quotes, backticks, or
    $(...) can't break out of the command or execute anything unintended —
    unlike building a shell string via run_bash(f'git commit -m "{message}"'),
    which was vulnerable to exactly that.
    """
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


# --- Tool schema, passed to the LLM's `tools` parameter ------------------
TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file's contents. Optionally give line_start/line_end (1-indexed, "
                        "inclusive) to read only part of a large file instead of the whole thing.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the file"},
            "line_start": {"type": "integer", "description": "First line to read (1-indexed), optional"},
            "line_end": {"type": "integer", "description": "Last line to read (inclusive), optional"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create a new file or completely overwrite an existing one with new content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Make a targeted find-and-replace edit in an existing file. "
                        "Prefer this over write_file for small changes to save tokens.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string", "description": "Exact text to find"},
            "new_text": {"type": "string", "description": "Text to replace it with"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "list_directory",
        "description": "List files and folders at a given path (default current directory).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."}
        }},
    }},
    {"type": "function", "function": {
        "name": "search_codebase",
        "description": "Search for a text string (or regex pattern) across all files under a directory.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "root": {"type": "string", "default": "."},
            "extensions": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['.py', '.js']"},
            "use_regex": {"type": "boolean", "default": False, "description": "Treat query as a regex pattern instead of plain text"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "read_files",
        "description": "Read multiple files in one call instead of one call per file — use this "
                        "when you already know you need several specific files.",
        "parameters": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
        }, "required": ["paths"]},
    }},
    {"type": "function", "function": {
        "name": "revert_file",
        "description": "Restore a file to its last git-committed state, discarding uncommitted "
                        "changes. Use this to safely undo an edit that made things worse.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "Run a shell command (tests, builds, package installs, git, etc). "
                        "Destructive commands will ask for confirmation.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "detect_and_run_tests",
        "description": "Auto-detect the project's test setup (pytest or npm test) and run it. "
                        "Call this after making code changes, before declaring a task done, "
                        "whenever the project has a test suite.",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string", "default": "."}
        }},
    }},
    {"type": "function", "function": {
        "name": "write_todos",
        "description": "Create or update a visible task plan/checklist for the current work. "
                        "Call this at the start of any multi-step task (3+ distinct steps) to lay "
                        "out the plan, then call it again to update statuses as steps complete. "
                        "Skip this for simple one- or two-step tasks.",
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
        "description": "Stage all changes and commit them with a message.",
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
