"""
provider_pool.py — multi-key LLM rotation with native tool-calling support.

UPDATED: Retry-After aware cooldown.
  - On a 429, reads the Retry-After header (seconds, or an HTTP-date) if present
    and cools the slot down for exactly that long instead of a flat 60s.
  - Falls back to the previous flat default only when no usable header is sent.
"""

import os
import time
import threading
import requests
import json
from email.utils import parsedate_to_datetime


def _load_keys(env_prefix):
    """Load <PREFIX>, then <PREFIX>_2, <PREFIX>_3, ... until one is missing."""
    keys = []
    primary = os.environ.get(env_prefix)
    if primary:
        keys.append(primary)
    i = 2
    while True:
        k = os.environ.get(f"{env_prefix}_{i}")
        if not k:
            break
        keys.append(k)
        i += 1
    return keys


CEREBRAS_KEYS = _load_keys("CEREBRAS_API_KEY")
GROQ_KEYS = _load_keys("GROQ_API_KEY")
OPENROUTER_KEYS = _load_keys("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-120b")

# Used only when a 429 response doesn't include a usable Retry-After.
DEFAULT_RATE_LIMIT_COOLDOWN = 60
DEFAULT_NETWORK_ERROR_COOLDOWN = 10
# Sane bounds so a malformed/huge header can't freeze a slot for hours.
MIN_COOLDOWN = 1
MAX_COOLDOWN = 300


def _build_pool():
    pool = [{"provider": "cerebras", "key": k, "model": CEREBRAS_MODEL} for k in CEREBRAS_KEYS]
    pool += [{"provider": "openrouter", "key": k, "model": OPENROUTER_MODEL} for k in OPENROUTER_KEYS]
    return pool


PROVIDER_POOL = _build_pool()

_key_lock = threading.Lock()
_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(PROVIDER_POOL))}
_groq_key_lock = threading.Lock()
_groq_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(GROQ_KEYS))}


def _parse_retry_after(resp, default_seconds=DEFAULT_RATE_LIMIT_COOLDOWN):
    """
    Parse a Retry-After header from a response. Supports both forms:
      - delay-seconds: "Retry-After: 30"
      - HTTP-date:      "Retry-After: Wed, 21 Oct 2026 07:28:00 GMT"
    Returns a clamped, safe number of seconds to cool down for.
    """
    header = resp.headers.get("Retry-After") if resp is not None else None
    if not header:
        return default_seconds

    header = header.strip()
    # Try plain integer seconds first
    try:
        seconds = float(header)
        return max(MIN_COOLDOWN, min(MAX_COOLDOWN, seconds))
    except ValueError:
        pass

    # Fall back to HTTP-date form
    try:
        target_dt = parsedate_to_datetime(header)
        seconds = target_dt.timestamp() - time.time()
        return max(MIN_COOLDOWN, min(MAX_COOLDOWN, seconds))
    except (TypeError, ValueError):
        return default_seconds


def _pick_available_index(state, lock, pool_len):
    """Prefer idle+off-cooldown → off-cooldown → soonest-free. Marks in_use atomically."""
    now = time.time()
    with lock:
        not_cooling = [i for i in range(pool_len) if state[i]["cooldown_until"] <= now]
        idle = [i for i in not_cooling if not state[i]["in_use"]]
        if idle:
            chosen = idle[0]
        elif not_cooling:
            chosen = not_cooling[0]
        else:
            chosen = min(range(pool_len), key=lambda i: state[i]["cooldown_until"])
        state[chosen]["in_use"] = True
        return chosen


def _mark_rate_limited(state, lock, index, cooldown_seconds=DEFAULT_RATE_LIMIT_COOLDOWN):
    with lock:
        state[index]["cooldown_until"] = time.time() + cooldown_seconds


def ask_ai(messages, tools=None, tool_choice="auto", max_tokens=4096):
    """
    Rotate across provider pool. Returns the raw message object,
    or {"error": "..."} on total failure.
    """
    if not PROVIDER_POOL and not GROQ_KEYS:
        return {"error": "No CEREBRAS_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY found in environment."}

    last_error = "unknown failure"

    for _ in range(len(PROVIDER_POOL)):
        idx = _pick_available_index(_key_state, _key_lock, len(PROVIDER_POOL))
        slot = PROVIDER_POOL[idx]
        try:
            url = (
                "https://api.cerebras.ai/v1/chat/completions"
                if slot["provider"] == "cerebras"
                else "https://openrouter.ai/api/v1/chat/completions"
            )
            payload = {"model": slot["model"], "messages": messages, "max_tokens": max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            try:
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {slot['key']}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=60,
                )
            except requests.exceptions.RequestException as e:
                _mark_rate_limited(_key_state, _key_lock, idx, cooldown_seconds=DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = f"could not reach {slot['provider']} ({e})"
                continue

            if resp.status_code == 429:
                cooldown = _parse_retry_after(resp)
                _mark_rate_limited(_key_state, _key_lock, idx, cooldown_seconds=cooldown)
                last_error = f"rate limited on {slot['provider']} slot #{idx + 1} (cooling {cooldown:.0f}s)"
                continue

            try:
                data = resp.json()
            except Exception:
                _mark_rate_limited(_key_state, _key_lock, idx, cooldown_seconds=DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = f"could not parse {slot['provider']} response"
                continue

            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    cooldown = _parse_retry_after(resp)
                    _mark_rate_limited(_key_state, _key_lock, idx, cooldown_seconds=cooldown)
                    last_error = f"{slot['provider']} slot #{idx + 1}: {data}"
                    continue
                return {"error": f"API error from {slot['provider']}: {data}"}

            return data["choices"][0]["message"]

        finally:
            with _key_lock:
                _key_state[idx]["in_use"] = False

    # Groq fallback tier
    for _ in range(len(GROQ_KEYS)):
        idx = _pick_available_index(_groq_key_state, _groq_key_lock, len(GROQ_KEYS))
        api_key = GROQ_KEYS[idx]
        try:
            payload = {"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens}
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=60,
                )
            except requests.exceptions.RequestException as e:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown_seconds=DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = f"could not reach Groq ({e})"
                continue

            if resp.status_code == 429:
                cooldown = _parse_retry_after(resp)
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown_seconds=cooldown)
                last_error = f"rate limited on Groq key #{idx + 1} (cooling {cooldown:.0f}s)"
                continue

            try:
                data = resp.json()
            except Exception:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown_seconds=DEFAULT_NETWORK_ERROR_COOLDOWN)
                last_error = "could not parse Groq response"
                continue

            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    cooldown = _parse_retry_after(resp)
                    _mark_rate_limited(_groq_key_state, _groq_key_lock, idx, cooldown_seconds=cooldown)
                    last_error = f"Groq key #{idx + 1}: {data}"
                    continue
                return {"error": f"Groq error: {data}"}

            return data["choices"][0]["message"]

        finally:
            with _groq_key_lock:
                _groq_key_state[idx]["in_use"] = False

    return {"error": last_error}
