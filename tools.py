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


# ============================================================
# File tools
# ============================================================

SNAPSHOT_DIR = ".agent_snapshots"


def _snapshot_before_write(path):
    """Copy current file contents to .agent_snapshots/<path>.<timestamp> before overwriting.
    Silently no-ops if the file doesn't exist yet (nothing to snapshot)."""
    if not os.path.exists(path):
        return
    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        safe_name = path.replace("/", "__")
        ts = int(time.time() * 1000)
        dest = os.path.join(SNAPSHOT_DIR, f"{safe_name}.{ts}.bak")
        shutil.copy2(path, dest)
    except OSError:
        pass  # snapshotting is best-effort, never block the real write


def read_file(path, line_start=None, line_end=None):
    """
    Reads a file. If line_start/line_end are given (1-indexed, inclusive),
    returns only that range — cheaper on large files.
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
    """Atomic write — temp file + os.replace(). Snapshots the previous version first."""
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
    """Targeted find-and-replace. old_text must match EXACTLY ONCE."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."
    content = read_file(path)
    occurrences = content.count(old_text)
    if occurrences == 0:
        return f"Could not find that exact text in {path}. No changes made. " \
               f"Tip: view the file first to copy the exact text to replace."
    if occurrences > 1:
        return f"ERROR: that text appears {occurrences} times in {path} — edit_file " \
               f"requires an exact, unique match. Include more surrounding context to " \
               f"make old_text unique, then try again."
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


def _ripgrep_available():
    return shutil.which("rg") is not None


def _search_with_ripgrep(query, root, extensions, use_regex):
    """Fast path: shell out to ripgrep if it's installed. Returns None on any failure
    so the caller can fall back to the pure-Python scan instead of erroring out."""
    cmd = ["rg", "--line-number", "--no-heading", "--max-count", "100"]
    if not use_regex:
        cmd.append("--fixed-strings")
        cmd.append("--ignore-case")
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

    # exit code 1 = no matches (not an error); 2+ = actual ripgrep error -> fall back
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
    r"""Search for a text string (or regex) across files under root. extensions e.g. [".py", ".js"].
    Uses ripgrep automatically if installed (much faster on large codebases); otherwise falls
    back to a pure-Python line-by-line scan."""
    if _ripgrep_available():
        rg_result = _search_with_ripgrep(query, root, extensions, use_regex)
        if rg_result is not None:
            return rg_result
        # fall through to pure-Python scan if ripgrep failed for any reason

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
    """Read multiple files in one call. Caps combined output at 20 000 chars."""
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
    """Restore a file to its last git-committed state. Falls back to the most recent
    snapshot in .agent_snapshots/ if the file isn't tracked by git."""
    if not os.path.exists(path):
        return f"ERROR: {path} does not exist."

    check = run_bash(f"git ls-files --error-unmatch {path}", confirmed=True)
    if "exit code 0" in check:
        result = run_bash(f"git checkout -- {path}", confirmed=True)
        if "exit code 0" in result:
            return f"Reverted {path} to its last committed state."
        return f"ERROR: git revert failed:\n{result}"

    # Not git-tracked — try the newest snapshot instead
    safe_name = path.replace("/", "__")
    if not os.path.isdir(SNAPSHOT_DIR):
        return f"ERROR: '{path}' is not tracked by git and no snapshots exist to revert to."
    candidates = sorted(
        (f for f in os.listdir(SNAPSHOT_DIR) if f.startswith(safe_name + ".")),
        reverse=True,
    )
    if not candidates:
        return f"ERROR: '{path}' is not tracked by git and no snapshots exist to revert to."
    latest = os.path.join(SNAPSHOT_DIR, candidates[0])
    shutil.copy2(latest, path)
    return f"'{path}' was not git-tracked — restored from snapshot {candidates[0]}."


def detect_and_run_tests(root="."):
    """Auto-detect the project's test setup (pytest or npm test) and run it."""
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


_VALID_STATUSES = {"pending", "in_progress", "completed"}


def write_todos(todos):
    """Create or update a visible task plan/checklist for the current work."""
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
    """Stage all changes and commit. Injection-safe: subprocess list, not shell string."""
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


# ============================================================
# Sandboxed run_bash
# ============================================================

# Only these binaries may run at all — checked against every segment of a
# chained command (a; b && c | d), not just the first token of the whole string.
_ALLOWED_BINARIES = {
    "git", "python3", "python", "pip", "pip3", "pytest", "npm", "npx", "node",
    "ls", "cat", "grep", "find", "mkdir", "cp", "mv", "echo", "pwd", "cd",
    "chmod", "touch", "diff", "wc", "head", "tail", "sort", "uniq", "sed",
    "awk", "tar", "unzip", "zip", "curl", "which", "env", "sleep", "true", "false",
}

# Splits a shell string on chaining/piping/substitution operators so each
# segment's leading binary can be individually validated against the allowlist.
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
    """
    Return the list of leading binaries across a (possibly chained) shell string,
    or None if the string can't be safely parsed (e.g. unbalanced quotes) —
    which run_bash treats as a reject.
    """
    segments = [s.strip() for s in _SHELL_METACHAR_SPLIT.split(command) if s.strip()]
    binaries = []
    for seg in segments:
        try:
            tokens = shlex.split(seg)
        except ValueError:
            return None
        if tokens:
            # strip a leading path, e.g. /usr/bin/python3 -> python3
            binaries.append(os.path.basename(tokens[0]))
    return binaries


def _check_allowlist(command):
    """Returns None if OK, or an error string if some binary in the command isn't allowed."""
    binaries = _extract_binaries(command)
    if binaries is None:
        return f"BLOCKED: command could not be safely parsed (check quoting): '{command}'"
    if not binaries:
        return "BLOCKED: no runnable command found."
    disallowed = [b for b in binaries if b not in _ALLOWED_BINARIES]
    if disallowed:
        return (f"BLOCKED: command uses disallowed binary(ies) {sorted(set(disallowed))}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_BINARIES))}. "
                f"If this is a legitimate need, ask the user to add it to the allowlist.")
    return None


# Resource caps applied to the subprocess via preexec_fn (Unix only).
_CPU_SECONDS_LIMIT = 30          # max CPU time
_MEMORY_BYTES_LIMIT = 1 * 1024 * 1024 * 1024  # 1 GB address space
_MAX_PROCESSES = 64              # cap forked/spawned children (fork-bomb guard)


def _apply_resource_limits():
    """preexec_fn target — runs in the child process before exec()."""
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
    Runs a shell command inside a sandbox:
      1. Hard-blocked catastrophic patterns are refused outright.
      2. Every binary used (including inside chains/pipes) must be on the allowlist.
      3. Destructive-looking-but-allowed commands require confirmed=True.
      4. The subprocess itself is capped on CPU time, memory, and process count.
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
        return (f"CONFIRMATION_REQUIRED: '{stripped}' looks destructive. "
                f"Re-run with confirmation if you're sure.")

    try:
        result = subprocess.run(
            stripped,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_apply_resource_limits if os.name == "posix" else None,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except OSError as e:
        return f"ERROR: could not run command (resource limit or OS error): {e}"

    output = (result.stdout or "") + (result.stderr or "")
    output = output[-4000:]
    return f"(exit code {result.returncode})\n{output}"


# ============================================================
# Tool schema registration
# ============================================================

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
        "description": "Create a new file or completely overwrite an existing one with new content. "
                        "The previous version is snapshotted to .agent_snapshots/ first.",
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
        "description": "Search for a text string (or regex pattern) across all files under a directory. "
                        "Uses ripgrep automatically if installed for speed, otherwise a pure-Python scan. "
                        "For finding files by name/pattern instead of content, use glob_files.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "root": {"type": "string", "default": "."},
            "extensions": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['.py', '.js']"},
            "use_regex": {"type": "boolean", "default": False},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "read_files",
        "description": "Read multiple files in one call instead of one call per file.",
        "parameters": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
        }, "required": ["paths"]},
    }},
    {"type": "function", "function": {
        "name": "revert_file",
        "description": "Restore a file to its last git-committed state, or (if not git-tracked) "
                        "to its most recent auto-snapshot.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "Run a shell command (tests, builds, package installs, git, etc), inside a "
                        "sandbox: only allowlisted binaries can run, and the process is capped on "
                        "CPU time/memory/process count. Destructive commands ask for confirmation.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "detect_and_run_tests",
        "description": "Auto-detect the project's test setup (pytest or npm test) and run it.",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string", "default": "."}
        }},
    }},
    {"type": "function", "function": {
        "name": "write_todos",
        "description": "Create or update a visible task plan/checklist for the current work. "
                        "Call at the start of any multi-step task (3+ steps), then update statuses as steps complete.",
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
