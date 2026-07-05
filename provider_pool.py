"""provider_pool.py
-------------------
Provides the :func:`ask_ai` function which abstracts access to multiple large language model providers.

The module loads API keys for Cerebras, OpenRouter, Groq and Gemini, builds a pool of provider slots, and
rotates requests across these slots while handling rate‑limiting and concurrency via per‑slot locks.

It also supports passing a ``tools`` schema to enable native function calling on providers that support it.
"""

import os
import time
import threading
import requests
import json


def _load_keys(env_prefix):
    """Same loading pattern as _load_cerebras_keys()/_load_groq_keys() in main-11.py:
    <PREFIX>, then <PREFIX>_2, <PREFIX>_3, ... until one is missing."""
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


def _build_pool():
    """Same idea as _build_provider_pool() in main-11.py: one slot per key,
    Cerebras first (fastest), then OpenRouter, pooled together as
    interchangeable rotation slots. Groq stays a separate fallback tier,
    same as your original design."""
    pool = [{"provider": "cerebras", "key": k, "model": CEREBRAS_MODEL} for k in CEREBRAS_KEYS]
    pool += [{"provider": "openrouter", "key": k, "model": OPENROUTER_MODEL} for k in OPENROUTER_KEYS]
    return pool


PROVIDER_POOL = _build_pool()

_key_lock = threading.Lock()
_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(PROVIDER_POOL))}

_groq_key_lock = threading.Lock()
_groq_key_state = {i: {"cooldown_until": 0.0, "in_use": False} for i in range(len(GROQ_KEYS))}


def _pick_available_index(state, lock, pool_len):
    """Shared logic behind _pick_available_key_index()/_pick_available_groq_key_index()
    in main-11.py: prefer idle+off-cooldown, else off-cooldown, else whichever
    frees up soonest. Marks it in_use atomically in the same critical section."""
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


def _mark_rate_limited(state, lock, index, cooldown_seconds=60):
    with lock:
        state[index]["cooldown_until"] = time.time() + cooldown_seconds


def ask_ai(messages, tools=None, tool_choice="auto", max_tokens=4096):
    """
    Core call, rotating across the provider pool exactly like main-11.py's
    ask_ai(), but taking a full `messages` list (so multi-turn tool-calling
    conversations work) and an optional `tools` schema.

    Returns the raw `message` object from the API response (which may have
    .content and/or .tool_calls), or a dict {"error": "..."} on total failure —
    callers check for "error" the same way main-11.py checks for strings
    starting with "ERROR:".
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
                last_error = f"could not reach {slot['provider']} ({e})"
                continue

            if resp.status_code == 429:
                _mark_rate_limited(_key_state, _key_lock, idx)
                last_error = f"rate limited on {slot['provider']} slot #{idx + 1}"
                continue

            try:
                data = resp.json()
            except Exception:
                last_error = f"could not parse {slot['provider']} response"
                continue

            if "choices" not in data:
                text = str(data).lower()
                if any(w in text for w in ("rate", "quota", "too_many", "queue_exceeded")):
                    _mark_rate_limited(_key_state, _key_lock, idx)
                    last_error = f"{slot['provider']} slot #{idx + 1}: {data}"
                    continue
                return {"error": f"API error from {slot['provider']}: {data}"}

            return data["choices"][0]["message"]

        finally:
            with _key_lock:
                _key_state[idx]["in_use"] = False

    # Fallback tier: Groq, same as main-11.py
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
                last_error = f"could not reach Groq ({e})"
                continue

            if resp.status_code == 429:
                _mark_rate_limited(_groq_key_state, _groq_key_lock, idx)
                last_error = f"rate limited on Groq key #{idx + 1}"
                continue

            try:
                data = resp.json()
            except Exception:
                last_error = "could not parse Groq response"
                continue

            if "choices" not in data:
                return {"error": f"Groq error: {data}"}

            return data["choices"][0]["message"]

        finally:
            with _groq_key_lock:
                _groq_key_state[idx]["in_use"] = False

    return {"error": last_error}
