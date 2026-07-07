"""
structural_search.py — AST-aware code search for Python files.

Adds four tools that go beyond grep:
  - find_definition   : where is a function/class actually defined?
  - find_callers      : every call site of a function/method, with surrounding context
  - find_references   : every usage of a name at all (calls, assignments, attribute access)
  - outline_file      : a structural map of one file — classes, methods, functions, line numbers

Pure stdlib (ast + os), no new dependencies. Python-only for now — .js/.ts files
are silently skipped since a JS AST would need an extra parser dependency.
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
    """Returns (tree, source_lines) or (None, error_string) on failure."""
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
    """
    Find every function or class definition matching `name` across all .py files
    under root. Returns file:line plus the signature line and (if present) the
    first line of the docstring.
    """
    results = []
    errors = []
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
    """
    Find every place `name` is called as a function or method (e.g. name(...) or
    obj.name(...)) across all .py files under root. Returns file:line with the
    calling line's source text for context.
    """
    results = []
    errors = []
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
    """
    Find every usage of `name` anywhere in the AST — calls, plain reads, writes,
    attribute access, import aliases. Broader (and noisier) than find_callers;
    useful before a rename to see the full blast radius.
    """
    results = []
    errors = []
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
    """
    Structural map of a single Python file: every top-level and nested class,
    method, and function with its line number and first docstring line.
    """
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
                entries.append(f"{prefix}line {child.lineno}: {async_tag}def {child.name}({', '.join(args)}){doc_preview}")
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
        shown_errors = errors[:5]
        base += "\n\n(skipped files with errors:\n  " + "\n  ".join(shown_errors) + ")"
    return base


STRUCTURAL_SEARCH_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "find_definition",
        "description": "AST-aware search: find where a function or class named `name` is actually "
                        "defined across all .py files under root. More precise than grep — won't "
                        "match comments, strings, or unrelated identical text.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "function or class name to find"},
            "root": {"type": "string", "default": "."},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "find_callers",
        "description": "AST-aware search: find every place a function or method named `name` is "
                        "actually called (name(...) or obj.name(...)) across all .py files under "
                        "root. Use before changing a function's signature or behavior to see every "
                        "call site that would be affected.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "root": {"type": "string", "default": "."},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "find_references",
        "description": "AST-aware search: find every usage of `name` anywhere — calls, reads, "
                        "writes, attribute access, imports. Broader than find_callers; use before "
                        "renaming something to see the full blast radius.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
            "root": {"type": "string", "default": "."},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "outline_file",
        "description": "Return a structural map of one Python file — every class, method, and "
                        "function with its line number and first docstring line. Use this to "
                        "understand a large file's shape before deciding what to read in full.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
]

STRUCTURAL_SEARCH_TOOL_FUNCTIONS = {
    "find_definition": find_definition,
    "find_callers": find_callers,
    "find_references": find_references,
    "outline_file": outline_file,
}
