"""
mcp_tools.py — Model Context Protocol (MCP) client support.

Connects to MCP servers defined in mcp_servers.json (same shape as the
Claude Desktop / Claude Code config file) and exposes every tool they offer
as a first-class agent tool, using the server's own JSON-Schema directly as
the OpenAI-style 'parameters' schema.

Requires: pip install mcp

Config file format (mcp_servers.json), in the project root:
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allow"]
    },
    "my-remote-server": {
      "url": "https://example.com/mcp"
    }
  }
}

"command" (+ "args", + "env")  -> stdio server, spawned as a subprocess
"url"                          -> streamable-HTTP server, connected over the network

Usage from the agent's own tool-calling loop:
  1. connect_mcp_servers()   — connects everything in mcp_servers.json
  2. list_mcp_tools()        — see what got discovered
  3. call the per-tool functions directly (e.g. mcp_filesystem_read_file),
     which are hot-reloaded into the live tool registry right after step 1.
"""

import asyncio
import json
import os
import re
import threading

MCP_CONFIG_FILE = "mcp_servers.json"
_TOOL_CALL_TIMEOUT = 60
_CONNECT_TIMEOUT = 30

_state = {
    "loop": None,
    "thread": None,
    "sessions": {},          # server_name -> ClientSession
    "exit_stack": None,      # single AsyncExitStack holding all open connections
    "tools_by_server": {},   # server_name -> list of mcp.types.Tool
    "connect_errors": {},    # server_name -> error string
}
_lock = threading.Lock()


def _ensure_loop():
    """Start a persistent background asyncio event loop, once, like a daemon thread."""
    with _lock:
        if _state["loop"] is not None and _state["thread"].is_alive():
            return _state["loop"]

        loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        _state["loop"] = loop
        _state["thread"] = thread
        return loop


def _run_coro(coro, timeout=_TOOL_CALL_TIMEOUT):
    """Schedule a coroutine on the background loop and block for its result."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def _load_config(config_path=MCP_CONFIG_FILE):
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {"__error__": f"Could not read {config_path}: {e}"}
    return data.get("mcpServers", {})


async def _connect_one(name, server_config, exit_stack):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    if "url" in server_config:
        from mcp.client.streamable_http import streamablehttp_client
        read, write, _ = await exit_stack.enter_async_context(
            streamablehttp_client(server_config["url"])
        )
    elif "command" in server_config:
        # Always inherit the full parent environment, then layer any config-specified
        # overrides on top. Passing env=None to the MCP SDK does NOT inherit the shell's
        # environment — it substitutes a minimal restricted default set (PATH, HOME, etc)
        # for security, which is missing vars some subprocess wrappers actually need.
        env = {**os.environ, **(server_config.get("env") or {})}

        # On Nix-based environments (e.g. Replit), npx's wrapper script requires
        # XDG_CONFIG_HOME (and sometimes its siblings) to be SET, even to an empty-ish
        # default — it's read with `set -u`, so an unset var is a hard crash regardless
        # of whether the parent process happened to inherit it from somewhere. Rather
        # than hope it's present in os.environ, guarantee sane defaults here.
        home = env.get("HOME", os.path.expanduser("~"))
        env.setdefault("XDG_CONFIG_HOME", os.path.join(home, ".config"))
        env.setdefault("XDG_CACHE_HOME", os.path.join(home, ".cache"))
        env.setdefault("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
        # Make sure the directories actually exist — some tools assume the path is
        # not just set but real, and will fail differently (but still fail) otherwise.
        for _xdg_dir in (env["XDG_CONFIG_HOME"], env["XDG_CACHE_HOME"], env["XDG_DATA_HOME"]):
            try:
                os.makedirs(_xdg_dir, exist_ok=True)
            except OSError:
                pass  # best-effort — if this fails, the real error will surface from npx itself

        params = StdioServerParameters(
            command=server_config["command"],
            args=server_config.get("args", []),
            env=env,
        )
        read, write = await exit_stack.enter_async_context(stdio_client(params))
    else:
        raise ValueError(f"Server '{name}' config needs either 'command' or 'url'.")

    session = await exit_stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    tools_result = await session.list_tools()
    return session, tools_result.tools


async def _connect_all(configs):
    from contextlib import AsyncExitStack

    if _state["exit_stack"] is None:
        _state["exit_stack"] = AsyncExitStack()
        await _state["exit_stack"].__aenter__()

    connected = []
    for name, cfg in configs.items():
        if name in _state["sessions"]:
            continue
        try:
            session, tools = await _connect_one(name, cfg, _state["exit_stack"])
            _state["sessions"][name] = session
            _state["tools_by_server"][name] = tools
            _state["connect_errors"].pop(name, None)
            connected.append(f"{name} ({len(tools)} tools)")
        except Exception as e:
            _state["connect_errors"][name] = str(e)
    return connected


_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_]")


def _prefixed_name(server_name, tool_name):
    safe_server = _INVALID_NAME_CHARS.sub("_", server_name)
    safe_tool = _INVALID_NAME_CHARS.sub("_", tool_name)
    return f"mcp_{safe_server}_{safe_tool}"[:64]


def _make_tool_function(server_name, tool_name):
    def _call(**kwargs):
        return _call_tool_sync(server_name, tool_name, kwargs)
    _call.__name__ = _prefixed_name(server_name, tool_name)
    return _call


async def _call_tool_async(server_name, tool_name, arguments):
    session = _state["sessions"].get(server_name)
    if session is None:
        raise RuntimeError(f"No active connection to MCP server '{server_name}'.")
    return await session.call_tool(tool_name, arguments=arguments)


def _format_mcp_result(result):
    prefix = "ERROR: " if getattr(result, "isError", False) else ""
    parts = []
    for block in getattr(result, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(block.text)
        elif block_type == "image":
            parts.append(f"[image content: {getattr(block, 'mimeType', 'unknown type')}]")
        elif block_type == "resource":
            parts.append(f"[resource: {getattr(block, 'resource', '')}]")
        else:
            parts.append(str(block))
    text = "\n".join(parts) if parts else "(empty result)"
    return f"{prefix}{text}"


def _call_tool_sync(server_name, tool_name, arguments):
    try:
        result = _run_coro(_call_tool_async(server_name, tool_name, arguments))
        return _format_mcp_result(result)
    except Exception as e:
        return f"ERROR: MCP tool call '{server_name}.{tool_name}' failed: {e}"


def connect_mcp_servers(config_path=MCP_CONFIG_FILE):
    """
    Read mcp_servers.json and connect to every configured server that isn't
    already connected. Returns a human-readable summary. Safe to call again
    later to pick up newly added servers in the config file.
    """
    configs = _load_config(config_path)
    if "__error__" in configs:
        return configs["__error__"]
    if not configs:
        return (f"No MCP servers configured. Create {config_path} with an "
                f"\"mcpServers\" object to add some.")

    try:
        connected = _run_coro(_connect_all(configs), timeout=_CONNECT_TIMEOUT)
    except Exception as e:
        return f"ERROR: failed to connect to MCP servers: {e}"

    lines = []
    if connected:
        lines.append("Connected: " + ", ".join(connected))
    if _state["connect_errors"]:
        for name, err in _state["connect_errors"].items():
            lines.append(f"FAILED '{name}': {err}")
    if not lines:
        lines.append("All configured servers were already connected.")
    return "\n".join(lines)


def list_mcp_tools():
    """List every tool currently available across all connected MCP servers."""
    if not _state["tools_by_server"]:
        return "No MCP servers connected yet. Call connect_mcp_servers first."
    lines = []
    for server_name, tools in _state["tools_by_server"].items():
        lines.append(f"[{server_name}]")
        for t in tools:
            desc = (t.description or "").strip().splitlines()[0] if t.description else ""
            lines.append(f"  - {_prefixed_name(server_name, t.name)}: {desc}")
    return "\n".join(lines)


def build_mcp_tool_schema_and_functions():
    """
    Build OpenAI-compatible tool schema entries + callables for every tool
    currently discovered across connected MCP servers. Each server's own
    JSON-Schema (inputSchema) is used directly as the function 'parameters'.
    """
    schema_list = []
    functions = {}
    for server_name, tools in _state["tools_by_server"].items():
        for t in tools:
            fn_name = _prefixed_name(server_name, t.name)
            description = t.description or f"MCP tool '{t.name}' from server '{server_name}'."
            parameters = t.inputSchema or {"type": "object", "properties": {}}
            schema_list.append({
                "type": "function",
                "function": {
                    "name": fn_name,
                    "description": f"[MCP:{server_name}] {description}"[:1000],
                    "parameters": parameters,
                },
            })
            functions[fn_name] = _make_tool_function(server_name, t.name)
    return schema_list, functions


def disconnect_all_mcp():
    """Tear down every MCP server connection. Not exposed as an agent tool by default."""
    async def _close():
        if _state["exit_stack"] is not None:
            await _state["exit_stack"].__aexit__(None, None, None)

    try:
        _run_coro(_close(), timeout=15)
    except Exception:
        pass
    _state["sessions"].clear()
    _state["tools_by_server"].clear()
    _state["connect_errors"].clear()
    _state["exit_stack"] = None
    return "All MCP server connections closed."


# ---------------------------------------------------------------------------
# Static tools wired into agent.py at import time. The *dynamic* per-server
# tools (see build_mcp_tool_schema_and_functions) only exist after connecting,
# and are hot-reloaded into TOOL_SCHEMA/TOOL_FUNCTIONS the same way create_tool
# hot-reloads agent-created tools.
# ---------------------------------------------------------------------------

MCP_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "connect_mcp_servers",
        "description": "Connect to every MCP (Model Context Protocol) server configured in "
                        "mcp_servers.json that isn't already connected. Each server's tools "
                        "become available as regular agent tools immediately after this "
                        "succeeds. Safe to call again later to pick up newly added servers.",
        "parameters": {"type": "object", "properties": {
            "config_path": {"type": "string", "default": MCP_CONFIG_FILE},
        }},
    }},
    {"type": "function", "function": {
        "name": "list_mcp_tools",
        "description": "List every tool currently available across all connected MCP servers, "
                        "grouped by server.",
        "parameters": {"type": "object", "properties": {}},
    }},
]
MCP_TOOL_FUNCTIONS = {
    "connect_mcp_servers": connect_mcp_servers,
    "list_mcp_tools": list_mcp_tools,
}
