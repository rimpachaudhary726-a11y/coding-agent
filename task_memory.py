"""
task_memory.py — persistent memory across sessions, per project.

Each project directory gets its own .agent_memory.json (one file, lives
alongside your code, safe to .gitignore). Every completed task gets a short
summary appended, stored along with a Gemini embedding of the task text.
Next time you start a new cli.py run in the same project, the new task gets
embedded too, and past entries are ranked by real semantic similarity —
so "authentication broke" correctly recalls a past "fix login bug" entry
even though they share no words, same idea as your main project's
semantic_rank() and the town sim's memory.py.

Falls back to simple keyword overlap if GEMINI_API_KEY isn't set or a
call fails, so this never hard-breaks the agent — it just gets a little
less smart about recall.
"""

import json
import math
import os
import time
import requests

MEMORY_FILE = ".agent_memory.json"
MAX_STORED = 200       # don't let this grow unbounded
MAX_RETRIEVED = 5       # how many past summaries get fed into a new task

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GEMINI_EMBED_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_EMBED_MODEL}:embedContent?key={{key}}"
)


def _embed(text):
    """Returns an embedding vector for `text`, or None if unavailable/failed."""
    if not GEMINI_API_KEY:
        return None
    try:
        resp = requests.post(
            GEMINI_EMBED_URL.format(key=GEMINI_API_KEY),
            json={"model": f"models/{GEMINI_EMBED_MODEL}", "content": {"parts": [{"text": text}]}},
            timeout=15,
        )
        data = resp.json()
        return data.get("embedding", {}).get("values")
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


def _cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _load(memory_path):
    if not os.path.exists(memory_path):
        return []
    try:
        with open(memory_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(memory_path, entries):
    tmp = memory_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries[-MAX_STORED:], f, indent=2)
    os.replace(tmp, memory_path)


def add_task_summary(task, summary, memory_path=MEMORY_FILE):
    """Call this once a task finishes — stores a short record of what was asked
    and what actually happened, embedded for future semantic recall."""
    entries = _load(memory_path)
    entries.append({
        "task": task,
        "summary": summary,
        "timestamp": time.time(),
        "embedding": _embed(task),  # None if Gemini unavailable — handled at retrieval time
    })
    _save(memory_path, entries)


def retrieve_relevant(task, memory_path=MEMORY_FILE, top_k=MAX_RETRIEVED):
    """
    Returns the most relevant past entries for a new task.

    Uses real embedding cosine similarity when both the new task and stored
    entries have embeddings. Falls back to keyword-overlap scoring for any
    entry (or the whole store) that lacks an embedding — e.g. entries saved
    before GEMINI_API_KEY was set, or if this call's embedding request fails.
    """
    entries = _load(memory_path)
    if not entries:
        return []

    query_embedding = _embed(task)
    task_words = set(task.lower().split())

    def score(entry):
        if query_embedding and entry.get("embedding"):
            sim = _cosine_similarity(query_embedding, entry["embedding"])
            return (1, sim, entry["timestamp"])  # tier 1: real semantic score
        entry_words = set(entry["task"].lower().split())
        overlap = len(task_words & entry_words)
        return (0, overlap, entry["timestamp"])  # tier 0: keyword fallback

    ranked = sorted(entries, key=score, reverse=True)

    if query_embedding:
        # With real embeddings, a similarity floor matters more than "any overlap" —
        # otherwise everything gets pulled in as "relevant" at low confidence.
        relevant = [
            e for e in ranked
            if e.get("embedding") and _cosine_similarity(query_embedding, e["embedding"]) > 0.55
        ]
        if relevant:
            return relevant[:top_k]

    # Fallback path: keyword overlap only
    relevant = [e for e in ranked if len(task_words & set(e["task"].lower().split())) > 0]
    return relevant[:top_k]


def format_for_prompt(entries):
    if not entries:
        return ""
    lines = ["Relevant memory from past sessions in this project:"]
    for e in entries:
        lines.append(f"- Task: \"{e['task']}\" -> {e['summary']}")
    return "\n".join(lines)
