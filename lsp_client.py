"""
lsp_client.py — a real Language Server Protocol client.

Spawns `pylsp` (python-lsp-server) as a subprocess and speaks actual LSP
JSON-RPC over stdio: Content-Length framed messages, request/response
correlation by id, and async notification handling (diagnostics arrive as
a push notification, not a request/response).

Requires: pip install python-lsp-server  (add to requirements.txt)

Exposes four high-level tools for the agent:
  - lsp_get_diagnostics(path)              real semantic errors/warnings
  - lsp_hover(path, line, character)        type/docstring info at a position
  - lsp_go_to_definition(path, line, char)  jump to where a symbol is defined
  - lsp_restart()                           recover if the server wedges

Positions are 0-indexed (line 0 = first line), per the LSP spec — note this
differs from the 1-indexed line numbers used elsewhere in this project
(read_file, find_definition, etc). Tool descriptions call this out.
"""

import json
import os
import subprocess
import threading
import time
import shutil

_SERVER_CMD = ["pylsp"]
_REQUEST_TIMEOUT = 10
_DIAGNOSTICS_WAIT = 4  # how long to wait for the push notification after didOpen


class LSPError(Exception):
    pass


class LSPClient:
    """One LSP client per project root, holding one long-lived pylsp subprocess."""

    def __init__(self, root_path="."):
        self.root_path = os.path.abspath(root_path)
        self.process = None
        self._reader_thread = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending = {}        # request id -> {"event": Event, "result": ..., "error": ...}
        self._diagnostics = {}    # file uri -> list of diagnostic dicts
        self._diag_events = {}    # file uri -> Event, set when a fresh diagnostics push arrives
        self._open_docs = {}      # file uri -> version number
        self._started = False
        self._start_error = None

    # ---------- low-level framing ----------

    def _write_message(self, obj):
        body = json.dumps(obj).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        self.process.stdin.write(header + body)
        self.process.stdin.flush()

    def _read_message(self):
        """Reads one Content-Length framed message from the server's stdout."""
        headers = {}
        while True:
            line = self.process.stdout.readline()
            if not line:
                return None  # pipe closed — server died
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
        # Response to a request we sent
        if "id" in msg and ("result" in msg or "error" in msg):
            with self._lock:
                entry = self._pending.get(msg["id"])
            if entry:
                entry["result"] = msg.get("result")
                entry["error"] = msg.get("error")
                entry["event"].set()
            return

        # Notification from the server
        method = msg.get("method")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            uri = params.get("uri")
            diagnostics = params.get("diagnostics", [])
            with self._lock:
                self._diagnostics[uri] = diagnostics
                event = self._diag_events.get(uri)
                if event:
                    event.set()
        # Other notifications (logs, progress, etc.) are intentionally ignored.

    # ---------- request/notification helpers ----------

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

    # ---------- lifecycle ----------

    def start(self):
        if self._started:
            return
        if shutil.which("pylsp") is None:
            self._start_error = (
                "pylsp is not installed. Run: pip install python-lsp-server --break-system-packages"
            )
            raise LSPError(self._start_error)

        self.process = subprocess.Popen(
            _SERVER_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        root_uri = "file://" + self.root_path
        init_result = self._send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": True},
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "definition": {},
                },
            },
        })
        self._send_notification("initialized", {})
        self._started = True
        return init_result

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

    # ---------- document sync ----------

    def _uri_for(self, path):
        return "file://" + os.path.abspath(path)

    def _ensure_open(self, path):
        uri = self._uri_for(path)
        if uri in self._open_docs:
            # already open — bump version and resend so the server re-analyzes current content
            with open(path, "r", errors="ignore") as f:
                text = f.read()
            self._open_docs[uri] += 1
            self._send_notification("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": self._open_docs[uri]},
                "contentChanges": [{"text": text}],
            })
            return uri

        with open(path, "r", errors="ignore") as f:
            text = f.read()
        self._open_docs[uri] = 1
        with self._lock:
            self._diag_events[uri] = threading.Event()
        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri, "languageId": "python", "version": 1, "text": text,
            },
        })
        return uri

    # ---------- high-level operations ----------

    def get_diagnostics(self, path, timeout=_DIAGNOSTICS_WAIT):
        if not os.path.exists(path):
            return f"ERROR: {path} does not exist."
        uri = self._ensure_open(path)
        with self._lock:
            event = self._diag_events.setdefault(uri, threading.Event())
            event.clear()
        # Server pushes diagnostics asynchronously after didOpen/didChange — wait briefly.
        event.wait(timeout)
        with self._lock:
            diagnostics = self._diagnostics.get(uri, [])
        if not diagnostics:
            return f"No diagnostics reported for {path} (clean, or server hasn't analyzed it yet)."
        lines = []
        severity_names = {1: "ERROR", 2: "WARNING", 3: "INFO", 4: "HINT"}
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
            loc_uri = loc.get("uri", "")
            loc_path = loc_uri.replace("file://", "")
            start = loc.get("range", {}).get("start", {})
            out.append(f"{loc_path}:{start.get('line', 0) + 1}:{start.get('character', 0)}")
        return "\n".join(out)


# ---------- module-level singleton, lazily started on first use ----------

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
    """Real semantic diagnostics (undefined names, type issues, unused imports, etc) for one file."""
    try:
        client = _get_client()
    except LSPError as e:
        return f"ERROR: could not start LSP server — {e}"
    try:
        return client.get_diagnostics(path)
    except LSPError as e:
        return f"ERROR: {e}"


def lsp_hover(path, line, character):
    """Hover info (type, docstring) at a 0-indexed line/character position in a file."""
    try:
        client = _get_client()
    except LSPError as e:
        return f"ERROR: could not start LSP server — {e}"
    try:
        return client.hover(path, line, character)
    except LSPError as e:
        return f"ERROR: {e}"


def lsp_go_to_definition(path, line, character):
    """Jump to the definition of the symbol at a 0-indexed line/character position."""
    try:
        client = _get_client()
    except LSPError as e:
        return f"ERROR: could not start LSP server — {e}"
    try:
        return client.definition(path, line, character)
    except LSPError as e:
        return f"ERROR: {e}"


def lsp_restart():
    """Restart the LSP server if it's wedged or unresponsive."""
    try:
        client = _get_client()
        client.restart()
        return "LSP server restarted."
    except LSPError as e:
        return f"ERROR: could not restart LSP server — {e}"


LSP_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "lsp_get_diagnostics",
        "description": "Get real semantic diagnostics for a Python file from a live language server "
                        "(pylsp) — undefined names, type mismatches, unused imports, syntax issues. "
                        "This catches real bugs that AST search and grep can't (they're structural, "
                        "not semantic). Prefer this before detect_and_run_tests when you've just "
                        "edited a file, to catch obvious problems before spending a test run on them.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "lsp_hover",
        "description": "Get type/docstring info for the symbol at a specific position in a file, "
                        "via the language server. line and character are 0-indexed (LSP convention) "
                        "— line 0 is the first line of the file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer", "description": "0-indexed line number"},
            "character": {"type": "integer", "description": "0-indexed column"},
        }, "required": ["path", "line", "character"]},
    }},
    {"type": "function", "function": {
        "name": "lsp_go_to_definition",
        "description": "Jump to where the symbol at a specific position is defined, via the language "
                        "server (handles imports, inherited methods, etc — more capable than the "
                        "AST-only find_definition for cross-file/cross-import cases). line and "
                        "character are 0-indexed.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer"},
            "character": {"type": "integer"},
        }, "required": ["path", "line", "character"]},
    }},
    {"type": "function", "function": {
        "name": "lsp_restart",
        "description": "Restart the language server subprocess if it stops responding or seems "
                        "stuck. Use only if lsp_get_diagnostics/lsp_hover/lsp_go_to_definition keep "
                        "timing out.",
        "parameters": {"type": "object", "properties": {}},
    }},
]

LSP_TOOL_FUNCTIONS = {
    "lsp_get_diagnostics": lsp_get_diagnostics,
    "lsp_hover": lsp_hover,
    "lsp_go_to_definition": lsp_go_to_definition,
    "lsp_restart": lsp_restart,
}
