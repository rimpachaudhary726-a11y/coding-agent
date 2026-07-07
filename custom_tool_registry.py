"""
custom_tool_registry.py — lets the agent create its OWN new tools.

UPDATED: self-created tools now run with a hard timeout.
  - Validation-time exec() (when a tool is first created) is timeout-guarded.
  - Every call to a loaded custom tool at runtime is wrapped so a hanging
    tool can't stall the whole agent loop indefinitely.
  - Unix-only (uses SIGALRM); falls back to no timeout on other platforms.
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

# Hard timeout applied to:
#   1. the validation exec() when a new tool is created
#   2. every call to a loaded custom tool at runtime
TOOL_TIMEOUT_SECONDS = 10


class ToolTimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise ToolTimeoutError(f"Execution exceeded {TOOL_TIMEOUT_SECONDS}s timeout.")


def _run_with_timeout(func, *args, timeout=TOOL_TIMEOUT_SECONDS, **kwargs):
    """
    Runs func(*args, **kwargs) with a hard wall-clock timeout using SIGALRM.
    Unix-only — on platforms without SIGALRM, runs without a timeout guard.
    """
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
            # best-effort restore of any previously pending alarm
            signal.alarm(old_alarm)


def _wrap_with_timeout(fn, name):
    """Wraps a loaded custom tool function so any call to it is timeout-guarded."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return _run_with_timeout(fn, *args, timeout=TOOL_TIMEOUT_SECONDS, **kwargs)
        except ToolTimeoutError:
            return (f"ERROR: custom tool '{name}' was killed after exceeding "
                    f"{TOOL_TIMEOUT_SECONDS}s. It may be stuck in a loop or blocking call.")
        except Exception as e:
            return f"ERROR: custom tool '{name}' raised an exception: {e}"
    return wrapper


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
        # Timeout-guarded: a tool whose top-level code hangs (e.g. an infinite
        # loop at module scope) can't stall tool creation indefinitely.
        _run_with_timeout(
            exec, compile(tree, "<custom_tool>", "exec"), namespace,
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except ToolTimeoutError:
        return False, (f"Code took longer than {TOOL_TIMEOUT_SECONDS}s just to define "
                        f"(likely a hang at module scope) — not saved.")
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

    return (f"Tool '{name}' created and saved permanently. Available immediately and in all "
            f"future sessions. Every call to it is capped at {TOOL_TIMEOUT_SECONDS}s.")


def load_custom_tools():
    """Load all previously created tools. Returns (schema_list, functions_dict, errors_list).
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


CREATE_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "create_tool",
        "description": "Create and PERMANENTLY save a new tool/function when no existing tool "
                        "covers what the current task needs. Available immediately and in all "
                        "future sessions, and every call to it is capped at "
                        f"{TOOL_TIMEOUT_SECONDS}s. Only use when genuinely no combination of "
                        "existing tools can do the job.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "lowercase snake_case function name"},
            "description": {"type": "string"},
            "parameters_json": {"type": "string", "description": "OpenAI function-parameters schema as JSON string"},
            "code": {"type": "string", "description": "full Python function definition matching name"},
        }, "required": ["name", "description", "parameters_json", "code"]},
    }},
]
CREATE_TOOL_FUNCTIONS = {"create_tool": create_tool}
