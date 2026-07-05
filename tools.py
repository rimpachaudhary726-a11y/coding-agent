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


def read_file(path):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    try:
        with open(path, "r") as f:
            return f.read()
    except UnicodeDecodeError:
        return f"ERROR: {path} is not a text file (binary content)."


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
    """Targeted find-and-replace, same contract as main-11.py's edit_file():
    old_text must appear in the file, exactly once is safest but not enforced
    here (mirrors the original's simple .replace() behavior)."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    content = read_file(path)
    if old_text not in content:
        return f"Could not find that exact text in {path}. No changes made. " \
               f"Tip: view the file first to copy the exact text to replace."
    occurrences = content.count(old_text)
    new_content = content.replace(old_text, new_text)
    write_file(path, new_content)
    diff_preview = "\n".join(
        list(difflib.unified_diff(
            content.splitlines(), new_content.splitlines(),
            lineterm="", n=1
        ))[:20]
    )
    note = f" (replaced {occurrences} occurrence(s))" if occurrences > 1 else ""
    return f"Edited {path}{note}.\n{diff_preview}"


def list_directory(path="."):
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    entries = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        entries.append(("[dir] " if os.path.isdir(full) else "      ") + name)
    return "\n".join(entries) if entries else "(empty directory)"


def search_codebase(query, root=".", extensions=None):
    """Grep-style search across text files under root. extensions e.g. [".py", ".js"]."""
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
        for fname in filenames:
            if extensions and not any(fname.endswith(ext) for ext in extensions):
                continue
            full = os.path.join(dirpath, fname)
            try:
                with open(full, "r", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if query.lower() in line.lower():
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


def git_commit(message, add_all=True):
    if add_all:
        add_result = run_bash("git add -A", confirmed=True)
        if "exit code 0" not in add_result:
            return f"git add failed:\n{add_result}"
    return run_bash(f'git commit -m "{message}"', confirmed=True)


# --- Tool schema, passed to the LLM's `tools` parameter ------------------
TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read the full contents of a file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to the file"}
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
        "description": "Search for a text string across all files under a directory (like grep).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "root": {"type": "string", "default": "."},
            "extensions": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['.py', '.js']"},
        }, "required": ["query"]},
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
        "name": "git_commit",
        "description": "Stage all changes and commit them with a message.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"},
        }, "required": ["message"]},
    }},
]

TOOL_FUNCTIONS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_directory": list_directory,
    "search_codebase": search_codebase,
    "run_bash": run_bash,
    "detect_and_run_tests": detect_and_run_tests,
    "git_commit": git_commit,
}
