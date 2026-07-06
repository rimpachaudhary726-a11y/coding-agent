"""
custom_tool_registry.py — lets the agent create its OWN new tools when
none of the existing ones cover what a task needs, and persist them for
every future session.

How it works:
1. Agent calls `create_tool(name, description, parameters_json, code)`.
2. The code is validated BEFORE anything is saved: it must compile, and
   actually define a callable with the right name, in an isolated
   namespace — a bad tool never reaches disk.
3. Once valid, the function's source is appended to custom_tools.py, and
   its schema (name/description/parameters) is appended to
   custom_tools_schema.json.
4. Both files persist across sessions — next time cli.py starts, these
   tools load automatically alongside the built-in ones, no re-creation
   needed.
5. Within the SAME session, the new tool becomes usable on the very next
   turn — agent.py's TOOL_SCHEMA/TOOL_FUNCTIONS get updated in place right
   after create_tool runs, not just on next startup.

Safety notes:
- This does NOT sandbox the generated code beyond syntax/definition
  validation — a created tool runs with the same filesystem/shell access
  as every other tool here. That's consistent with how the rest of this
  project already works (run_bash has real shell access), but worth
  knowing: a tool the agent writes for itself is not inherently safer
  than one you wrote.
- Tool names are restricted to valid Python identifiers to prevent
  injection through the name field itself.
"""

import ast
import importlib
import json
import os
import re

CUSTOM_TOOLS_FILE = "custom_tools.py"
CUSTOM_SCHEMA_FILE = "custom_tools_schema.json"

_VALID_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

# Modules a self-created tool is allowed to import at definition time.
# This is NOT a full sandbox (see module docstring) — it's a cheap first
# gate that stops the obvious cases: a generated "tool" quietly importing
# os/subprocess/socket/etc. and doing something at exec() time, before the
# tool is ever even called. Deliberately includes the modules the agent's
# OWN built-in tools already rely on (json, re, math, etc.) since those are
# legitimately useful for most tools it would plausibly write; anything
# needing real filesystem/shell/network access should be a change to the
# core tools.py instead of a self-created tool.
_ALLOWED_IMPORTS = {
    "re", "json", "math", "time", "datetime", "collections", "itertools",
    "functools", "string", "textwrap", "difflib", "random", "statistics",
    "typing", "dataclasses", "enum", "decimal", "fractions",
}


def _check_import_safety(tree):
    """Walks the AST for Import/ImportFrom nodes outside _ALLOWED_IMPORTS.
    Returns an error message, or None if all imports are allowed (or there
    are none)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORTS:
                    return (f"Import of '{alias.name}' is not allowed in a self-created tool "
                            f"(allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}). "
                            f"If this tool genuinely needs filesystem/shell/network access, "
                            f"that belongs in tools.py as a reviewed built-in, not a self-created tool.")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                return (f"Import from '{node.module}' is not allowed in a self-created tool "
                        f"(allowed: {', '.join(sorted(_ALLOWED_IMPORTS))}).")
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
    """
    Checks, WITHOUT touching disk:
    1. The code is syntactically valid Python (ast.parse — safer than
       compile+exec for the first pass, catches syntax errors cleanly).
    2. It defines exactly a function (or assigns a callable) matching `name`.
    3. Executing it in an isolated namespace actually produces a callable
       under that name, with no import of anything destructive at
       module-level (a light check, not a full sandbox — see module docstring).

    Returns (ok: bool, error_message_or_None).
    """
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
    """
    The agent-facing function. Validates and, if valid, permanently saves
    a new tool.

    `parameters_json` — a JSON string matching the standard OpenAI-style
    function parameters schema, e.g.:
      '{"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}'
    `code` — the full Python function definition as a string, e.g.:
      "def double(x):\\n    return x * 2"
    """
    if not _VALID_NAME.match(name):
        return f"ERROR: '{name}' is not a valid tool name (use lowercase snake_case, e.g. 'my_tool')."

    try:
        parameters = json.loads(parameters_json)
    except json.JSONDecodeError as e:
        return f"ERROR: parameters_json is not valid JSON: {e}"

    ok, err = _validate_code_defines_callable(name, code)
    if not ok:
        return f"ERROR: tool not saved — {err}"

    _ensure_files()

    schema_list = _load_schema()
    schema_list = [s for s in schema_list if s["function"]["name"] != name]  # allow overwrite/fix
    schema_list.append({
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    })
    _save_schema(schema_list)

    with open(CUSTOM_TOOLS_FILE, "a") as f:
        f.write(f"\n\n# --- {name} ---\n{code}\n")

    return f"Tool '{name}' created and saved permanently. Available immediately and in all future sessions."


def load_custom_tools():
    """
    Loads every previously created custom tool from disk: the schema list
    (for TOOL_SCHEMA) and the actual functions (for TOOL_FUNCTIONS), by
    importing/reloading custom_tools.py.

    Returns (schema_list, functions_dict, errors_list). A tool whose code
    fails to load for some reason (e.g. the file was hand-edited badly)
    is skipped with an error reported, rather than crashing the whole
    agent on startup.
    """
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
            errors.append(f"Tool '{fn_name}' is in schema but missing/broken in {CUSTOM_TOOLS_FILE} — skipped.")
            continue
        functions[fn_name] = fn
        valid_schema.append(entry)

    return valid_schema, functions, errors


CREATE_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "create_tool",
        "description": "Create and PERMANENTLY save a new tool/function when no existing tool "
                        "covers what the current task needs. The tool becomes available "
                        "immediately in this session and automatically in all future sessions. "
                        "Only use this when genuinely no combination of existing tools can do "
                        "the job — prefer existing tools whenever possible.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "lowercase snake_case function name"},
            "description": {"type": "string", "description": "what this tool does, for future tool selection"},
            "parameters_json": {"type": "string",
                                 "description": "JSON string: standard OpenAI function-parameters "
                                                 "schema, e.g. '{\"type\":\"object\",\"properties\":"
                                                 "{\"x\":{\"type\":\"integer\"}},\"required\":[\"x\"]}'"},
            "code": {"type": "string", "description": "full Python function definition matching `name`"},
        }, "required": ["name", "description", "parameters_json", "code"]},
    }},
]
CREATE_TOOL_FUNCTIONS = {"create_tool": create_tool}
