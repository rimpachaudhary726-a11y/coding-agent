"""
provider_pool.py — multi-key LLM rotation with native tool-calling support.

This is your ask_ai() rotation logic from main-11.py, carried over almost
exactly (same key-loading pattern, same in_use/cooldown locking, same
Cerebras -> OpenRouter -> Groq fallback order), but extended with a
`tools` parameter so the model can make structured tool_calls instead of
just returning plain text. Your original ask_ai() never passed `tools`,
so it always got plain text back — this version keeps that codepath
working (call with tools=None) AND adds the new one.

gpt-oss-120b supports native function calling on Cerebras, Groq, and
OpenRouter, and Gemini supports it too via a different request shape,
so all four of your providers work here.
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


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _openai_messages_to_gemini(messages):
    """
    Translates an OpenAI-style messages list into Gemini's {contents, systemInstruction}
    shape. Gemini has no 'system' or 'tool' role in `contents` — system content becomes
    systemInstruction, and tool results become a 'user' turn with a functionResponse part
    (matched by function NAME, since Gemini doesn't use OpenAI's tool_call_id concept).

    Known limitation: if the same tool is called twice in one turn, Gemini's
    name-based matching can't disambiguate which result belongs to which call —
    this only matters for parallel identical tool calls, which is rare in practice.
    """
    system_instruction = None
    contents = []
    # Map tool_call_id -> function name, so later "tool" role messages can be
    # translated into Gemini's name-keyed functionResponse format.
    call_id_to_name = {}

    for msg in messages:
        role = msg.get("role")
        if role == "system":
            text = (system_instruction + "\n\n" if system_instruction else "") + (msg.get("content") or "")
            system_instruction = text
            continue

        if role == "user":
            contents.append({"role": "user", "parts": [{"text": msg.get("content") or ""}]})

        elif role == "assistant":
            parts = []
            if msg.get("content"):
                parts.append({"text": msg["content"]})
            for tc in (msg.get("tool_calls") or []):
                fn = tc["function"] if isinstance(tc, dict) else {"name": tc.function.name, "arguments": tc.function.arguments}
                tc_id = tc["id"] if isinstance(tc, dict) else tc.id
                try:
                    args = json.loads(fn["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                call_id_to_name[tc_id] = fn["name"]
                parts.append({"functionCall": {"name": fn["name"], "args": args}})
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            fn_name = call_id_to_name.get(msg.get("tool_call_id"), "unknown_function")
            contents.append({
                "role": "user",
                "parts": [{"functionResponse": {"name": fn_name, "response": {"result": msg.get("content")}}}],
            })

    return system_instruction, contents


def _openai_tools_to_gemini(tools):
    """Translates OpenAI-style `tools` schema into Gemini's functionDeclarations shape."""
    if not tools:
        return None
    declarations = [t["function"] for t in tools if t.get("type") == "function"]
    return [{"functionDeclarations": declarations}]


def _gemini_response_to_openai_message(data):
    """Translates a Gemini generateContent response back into the same
    {content, tool_calls} shape ask_ai() callers already expect from OpenAI-style
    providers, so Gemini is a drop-in swap — nothing downstream needs to know
    which provider actually answered."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        return {"content": None, "tool_calls": None}

    text_parts = []
    tool_calls = []
    for i, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": f"gemini_call_{i}",
                "type": "function",
                "function": {"name": fc["name"], "arguments": json.dumps(fc.get("args", {}))},
            })

    return {
        "content": "\n".join(text_parts) if text_parts else None,
        "tool_calls": tool_calls or None,
    }


def _call_gemini(messages, tools=None, max_tokens=4096):
    """Direct Gemini call, used both as ask_ai()'s final fallback tier and by
    task_memory.py's embedding calls (which use a different Gemini endpoint,
    unaffected by this)."""
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}

    system_instruction, contents = _openai_messages_to_gemini(messages)
    payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    gemini_tools = _openai_tools_to_gemini(tools)
    if gemini_tools:
        payload["tools"] = gemini_tools

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=60,
        )
    except requests.exceptions.RequestException as e:
        return {"error": f"could not reach Gemini ({e})"}

    if resp.status_code == 429:
        return {"error": "rate limited on Gemini"}

    try:
        data = resp.json()
    except Exception:
        return {"error": "could not parse Gemini response"}

    if "candidates" not in data:
        return {"error": f"Gemini error: {data}"}

    return _gemini_response_to_openai_message(data)


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

    # Final fallback tier: Gemini (translated to/from OpenAI-style format)
    if GEMINI_API_KEY:
        gemini_result = _call_gemini(messages, tools=tools, max_tokens=max_tokens)
        if "error" not in gemini_result:
            return gemini_result
        last_error = f"Gemini also failed: {gemini_result['error']}"

    return {"error": last_error}
