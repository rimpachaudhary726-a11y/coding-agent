"""
task_memory.py — persistent memory across sessions, per project.

UPDATED: importance-weighted ranking.
  - Each stored task summary now carries an `importance` score (1-10).
  - Ranking combines similarity/keyword-overlap tier with importance, so a
    highly important lesson from a while ago can outrank a recent-but-trivial one.
  - Importance is inferred automatically at save time (heuristic keyword scan)
    unless explicitly passed in.
"""

import json
import math
import os
import re
import time
import requests

MEMORY_FILE = ".agent_memory.json"
MAX_STORED = 200
MAX_RETRIEVED = 5
DEFAULT_IMPORTANCE = 5

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GEMINI_EMBED_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_EMBED_MODEL}:embedContent?key={{key}}"
)

# Heuristic keyword bumps used when importance isn't explicitly provided.
_HIGH_IMPORTANCE_MARKERS = [
    "critical", "bug", "fixed", "broke", "regression", "security", "data loss",
    "crash", "failed", "important", "never do", "do not", "gotcha", "careful",
    "corrupt", "irreversible",
]
_LOW_IMPORTANCE_MARKERS = ["typo", "minor", "cosmetic", "formatting", "rename"]


def _embed(text):
    """Returns an embedding vector for text, or None if unavailable/failed."""
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


def _infer_importance(task, summary):
    """Heuristic 1-10 importance score when the caller doesn't supply one explicitly."""
    text = f"{task} {summary}".lower()
    score = DEFAULT_IMPORTANCE
    if any(marker in text for marker in _HIGH_IMPORTANCE_MARKERS):
        score += 3
    if any(marker in text for marker in _LOW_IMPORTANCE_MARKERS):
        score -= 2
    return max(1, min(10, score))


def _load(memory_path):
    if not os.path.exists(memory_path):
        return []
    try:
        with open(memory_path, "r") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    # Backfill importance for entries saved before this update existed.
    changed = False
    for e in entries:
        if "importance" not in e:
            e["importance"] = _infer_importance(e.get("task", ""), e.get("summary", ""))
            changed = True
    if changed:
        _save(memory_path, entries)
    return entries


def _save(memory_path, entries):
    tmp = memory_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries[-MAX_STORED:], f, indent=2)
    os.replace(tmp, memory_path)


def add_task_summary(task, summary, memory_path=MEMORY_FILE, importance=None):
    """Append a completed task + its outcome to persistent memory.
    importance: optional explicit 1-10 score; auto-inferred from content if omitted."""
    entries = _load(memory_path)
    score = importance if importance is not None else _infer_importance(task, summary)
    score = max(1, min(10, score))
    entries.append({
        "task": task,
        "summary": summary,
        "timestamp": time.time(),
        "importance": score,
        "embedding": _embed(task),
    })
    _save(memory_path, entries)


def retrieve_relevant(task, memory_path=MEMORY_FILE, top_k=MAX_RETRIEVED):
    """
    Return the most relevant past entries for a new task.

    Ranking:
      Tier 1 (embedding cosine > 0.55): sorted by (importance, similarity) —
        importance breaks ties and can outweigh small similarity gaps, so a
        highly important but slightly-less-similar memory still surfaces.
      Tier 0 (keyword overlap fallback): sorted by (importance, overlap count).
    """
    entries = _load(memory_path)
    if not entries:
        return []

    query_embedding = _embed(task)
    task_words = set(task.lower().split())

    if query_embedding:
        scored = [
            (e, _cosine_similarity(query_embedding, e["embedding"]))
            for e in entries if e.get("embedding")
        ]
        relevant = [(e, sim) for e, sim in scored if sim > 0.55]
        if relevant:
            # Weight: importance (1-10, normalized) contributes as much as raw similarity,
            # so an important memory can beat a merely-more-similar trivial one.
            relevant.sort(
                key=lambda pair: (pair[1] * 0.6) + (pair[0]["importance"] / 10 * 0.4),
                reverse=True,
            )
            return [e for e, _ in relevant[:top_k]]

    def keyword_score(entry):
        overlap = len(task_words & set(entry["task"].lower().split()))
        return (entry.get("importance", DEFAULT_IMPORTANCE), overlap)

    candidates = [e for e in entries if len(task_words & set(e["task"].lower().split())) > 0]
    candidates.sort(key=keyword_score, reverse=True)
    return candidates[:top_k]


def format_for_prompt(entries):
    if not entries:
        return ""
    lines = ["Relevant memory from past sessions in this project:"]
    for e in entries:
        importance_tag = " [HIGH IMPORTANCE]" if e.get("importance", DEFAULT_IMPORTANCE) >= 8 else ""
        lines.append(f"- Task: \"{e['task']}\" -> {e['summary']}{importance_tag}")
    return "\n".join(lines)
