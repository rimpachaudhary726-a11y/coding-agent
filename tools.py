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


# ---------------------------------------------------------------------------
# Diff-before-apply: staged changes reviewed as a whole before hitting disk.
# ---------------------------------------------------------------------------

# path -> {"old": str, "new": str, "is_new_file": bool}
_STAGED_CHANGES = {}


def _current_staged_or_disk_content(path):
    """What the file looks like right now, accounting for any already-staged edit."""
    if path in _STAGED_CHANGES:
        return _STAGED_CHANGES[path]["new"]
    if os.path.exists(path):
        return read_file(path)
    return None


def stage_write_file(path, content):
    """
    Queue a full-file write without touching disk. Call review_pending_changes
    to see the diff, then apply_pending_changes to commit it.
    """
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
    """
    Queue a targeted find-and-replace without touching disk. Operates on top
    of any already-staged edit for this path, so multiple stage_edit_file
    calls on the same file compose correctly.
    """
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
    """Return a single unified diff covering every currently staged change."""
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
    """
    Write every staged change to disk in one pass. Requires confirmed=True —
    the agent harness (agent.py) is expected to gate this behind a human
    reviewing review_pending_changes() first.
    """
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
    """Throw away all staged changes without writing anything to disk."""
    if not _STAGED_CHANGES:
        return "No pending changes to discard."
    count = len(_STAGED_CHANGES)
    _STAGED_CHANGES.clear()
    return f"Discarded {count} staged change(s). Nothing was written to disk."


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
    {"type": "function", "function": {
        "name": "read_files",
        "description": "Read multiple files in one call. Combined output capped at 20,000 chars.",
        "parameters": {"type": "object", "properties": {
            "paths": {"type": "array", "items": {"type": "string"}},
        }, "required": ["paths"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write (overwrite or create) a file immediately, no review step. "
                        "Best for quick, single-file fixes. For multi-file or reviewed changes, "
                        "use stage_write_file + apply_pending_changes instead.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Find-and-replace in a file immediately, no review step. old_text must "
                        "match exactly once. For multi-file or reviewed changes, use "
                        "stage_edit_file + apply_pending_changes instead.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "stage_write_file",
        "description": "Queue a full-file write WITHOUT touching disk. Use this (instead of "
                        "write_file) when you want the change reviewed alongside other staged "
                        "changes before anything is actually written. Follow with "
                        "review_pending_changes and then apply_pending_changes.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "stage_edit_file",
        "description": "Queue a find-and-replace edit WITHOUT touching disk. Composes with any "
                        "prior staged edit to the same file. Use for multi-file or "
                        "want-it-reviewed-first changes; follow with review_pending_changes "
                        "then apply_pending_changes.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        }, "required": ["path", "old_text", "new_text"]},
    }},
    {"type": "function", "function": {
        "name": "review_pending_changes",
        "description": "Show a full unified diff of every currently staged (not yet written) "
                        "change, across all files. Call this before apply_pending_changes.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "apply_pending_changes",
        "description": "Write every staged change to disk in one pass. Requires human approval — "
                        "will return CONFIRMATION_REQUIRED until confirmed.",
        "parameters": {"type": "object", "properties": {
            "confirmed": {"type": "boolean", "default": False},
        }},
    }},
    {"type": "function", "function": {
        "name": "discard_pending_changes",
        "description": "Throw away all staged changes without writing anything to disk.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "list_directory",
        "description": "List files and folders at a path.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "default": "."},
        }},
    }},
    {"type": "function", "function": {
        "name": "search_codebase",
        "description": "Grep-style text or regex search across all files under a directory.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "root": {"type": "string", "default": "."},
            "extensions": {"type": "array", "items": {"type": "string"}},
            "use_regex": {"type": "boolean", "default": False},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "Run a shell command. Destructive-looking commands require confirmation.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "confirmed": {"type": "boolean", "default": False},
            "timeout": {"type": "integer", "default": 60},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "revert_file",
        "description": "git checkout -- <path> to undo a bad edit and restore the last committed state.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "detect_and_run_tests",
        "description": "Auto-detect and run the project's test suite (pytest or npm test).",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string", "default": "."},
        }},
    }},
    {"type": "function", "function": {
        "name": "write_todos",
        "description": "Create or update a live task checklist for multi-step work.",
        "parameters": {"type": "object", "properties": {
            "todos": {"type": "array", "items": {"type": "object", "properties": {
                "content": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            }, "required": ["content"]}},
        }, "required": ["todos"]},
    }},
    {"type": "function", "function": {
        "name": "git_commit",
        "description": "Stage all changes and commit with a message.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"},
            "add_all": {"type": "boolean", "default": True},
        }, "required": ["message"]},
    }},
]

TOOL_FUNCTIONS = {
    "read_file": read_file, "read_files": read_files,
    "write_file": write_file, "edit_file": edit_file,
    "stage_write_file": stage_write_file, "stage_edit_file": stage_edit_file,
    "review_pending_changes": review_pending_changes,
    "apply_pending_changes": apply_pending_changes,
    "discard_pending_changes": discard_pending_changes,
    "revert_file": revert_file, "list_directory": list_directory,
    "search_codebase": search_codebase, "run_bash": run_bash,
    "detect_and_run_tests": detect_and_run_tests,
    "write_todos": write_todos, "git_commit": git_commit,
}
