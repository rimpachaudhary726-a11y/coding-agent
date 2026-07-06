"""
Simple per‑agent memory store used by the generated tick_engine.
It stores a chronological list of memory entries (text, tick, importance)
in a JSON file and provides a tiny API:

    AgentMemoryStore(id).add(text=..., tick=..., importance=...)
    AgentMemoryStore(id).retrieve_relevant(current_tick, top_k)

The retrieve_relevant method returns the most important recent memories
as objects with a .text attribute, which the tick_engine formats into the
prompt.
"""

import json
import os
from dataclasses import dataclass
from typing import List


@dataclass
class MemoryItem:
    text: str
    tick: int
    importance: int


class AgentMemoryStore:
    """A very small persistent memory store for a single agent.

    The store is persisted to a JSON file named ``.agent_memory_{agent_id}.json``
    in the current working directory. Each entry is a dict with ``text``,
    ``tick`` and ``importance`` keys.
    """

    MAX_STORED = 200  # safety limit to keep file size reasonable

    def __init__(self, agent_id: str, memory_path: str | None = None):
        self.agent_id = agent_id
        self.path = memory_path or f".agent_memory_{agent_id}.json"
        self._entries: List[dict] = self._load()

    # ---------------------------------------------------------------------
    # Persistence helpers
    # ---------------------------------------------------------------------
    def _load(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self) -> None:
        # Keep only the most recent MAX_STORED entries
        to_save = self._entries[-self.MAX_STORED :]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def add(self, text: str, tick: int, importance: int = 5) -> None:
        """Append a new memory entry.

        ``importance`` is a crude numeric weight – higher values are considered
        more salient when retrieving relevant memories.
        """
        self._entries.append({"text": text, "tick": tick, "importance": importance})
        self._save()

    def retrieve_relevant(self, current_tick: int, top_k: int = 5) -> List[MemoryItem]:
        """Return up to ``top_k`` memories that occurred on or before ``current_tick``.

        The selection prioritises higher ``importance`` first and, for ties,
        more recent ``tick`` values. The returned objects are ``MemoryItem``
        instances so callers can access ``.text`` directly.
        """
        # Filter out any future entries (should not happen but defensive)
        candidates = [e for e in self._entries if e.get("tick", 0) <= current_tick]
        # Sort by importance descending, then tick descending (more recent)
        candidates.sort(key=lambda e: (e.get("importance", 0), e.get("tick", 0)), reverse=True)
        selected = candidates[:top_k]
        return [MemoryItem(text=e["text"], tick=e["tick"], importance=e.get("importance", 0)) for e in selected]
