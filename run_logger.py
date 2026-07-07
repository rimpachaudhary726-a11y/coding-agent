"""
run_logger.py — structured JSONL logging of every tool call the agent makes.

Each call appends one line to .agent_runs.jsonl:
    {"timestamp": ..., "tool": "write_file", "args": {...},
     "latency_ms": 42, "result_preview": "...", "error": false}

Kept deliberately dependency-free and best-effort: logging failures never
block or break the actual tool call.
"""

import json
import os
import time

RUN_LOG_PATH = ".agent_runs.jsonl"
MAX_ARG_PREVIEW = 300
MAX_RESULT_PREVIEW = 300


def _safe_preview(value, limit):
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def log_tool_call(tool_name, args, result, latency_ms, is_error=False):
    """Append one structured record for a single tool call. Best-effort — never raises."""
    try:
        record = {
            "timestamp": time.time(),
            "tool": tool_name,
            "args_preview": _safe_preview(args, MAX_ARG_PREVIEW),
            "latency_ms": round(latency_ms, 1),
            "result_preview": _safe_preview(result, MAX_RESULT_PREVIEW),
            "error": bool(is_error),
        }
        with open(RUN_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # logging must never break the agent


def summarize_recent(n=20):
    """Return the last n log records as a list of dicts, newest last. For debugging/eval use."""
    if not os.path.exists(RUN_LOG_PATH):
        return []
    try:
        with open(RUN_LOG_PATH, "r") as f:
            lines = f.readlines()
    except OSError:
        return []
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
