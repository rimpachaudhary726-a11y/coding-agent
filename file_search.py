"""
file_search.py — glob-style file discovery by name/path pattern (not content).

Complements search_codebase (content search) and list_directory (single-level
listing) with pattern-based recursive file finding, e.g. "**/*.py" or
"src/**/test_*.py". Results are sorted newest-modified-first, since that's
usually what you want when hunting for "the file I just touched".
"""

import os
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
_MAX_RESULTS = 200


def glob_files(pattern, root=".", max_results=_MAX_RESULTS):
    """
    Find files matching a glob pattern under root, e.g.:
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

    # Filter out anything living inside a skipped directory.
    filtered = []
    for m in matches:
        if m.is_dir():
            continue
        parts = set(m.parts)
        if parts & _SKIP_DIRS:
            continue
        filtered.append(m)

    if not filtered:
        return f"No files matched pattern '{pattern}' under {root}."

    try:
        filtered.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        pass  # if stat fails on something mid-sort, just skip sorting rather than crash

    truncated = len(filtered) > max_results
    filtered = filtered[:max_results]

    lines = [str(p) for p in filtered]
    result = "\n".join(lines)
    if truncated:
        result += f"\n... (truncated at {max_results} results — narrow the pattern for a full list)"
    return result


FILE_SEARCH_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "glob_files",
        "description": "Find files by name/path pattern (not content) — e.g. '**/*.py' for all "
                        "Python files at any depth, 'src/**/test_*.py' for test files under src/, "
                        "'*.json' for top-level JSON files. Results are sorted most-recently-"
                        "modified first. Use this instead of search_codebase when you know the "
                        "kind of file you want but not its content, or when you just need a file "
                        "listing (e.g. 'find all config files', 'what did I touch most recently').",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "glob pattern, e.g. '**/*.py'"},
            "root": {"type": "string", "default": "."},
        }, "required": ["pattern"]},
    }},
]

FILE_SEARCH_TOOL_FUNCTIONS = {
    "glob_files": glob_files,
}
