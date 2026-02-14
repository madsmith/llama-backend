from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .messages import conversation_hash, is_cacheable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache lookup result types
# ---------------------------------------------------------------------------


@dataclass
class CacheValid:
    """Base for cacheable conversations (hit or miss)."""

    cache_id: str

    def get_cache_id(self) -> str:
        return self.cache_id


@dataclass
class CacheHit(CacheValid):
    """The conversation was found in the cache."""

    time_created: float = 0.0
    time_accessed: float = 0.0
    last_slot_id: int | None = None


@dataclass
class CacheMiss(CacheValid):
    """The conversation is cacheable but not yet cached."""


@dataclass
class CacheInvalid:
    """The conversation is not cacheable."""

    reason: str


CacheResult = CacheHit | CacheMiss | CacheInvalid

# ---------------------------------------------------------------------------
# KVCache — JSON-file-backed, one per model/server
# ---------------------------------------------------------------------------


class KVCache:
    """Tracks cached conversation KV states for a single model server."""

    def __init__(self, slot_save_dir: Path) -> None:
        self._dir = slot_save_dir
        self._path = slot_save_dir / "kv_cache.json"
        self._entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._entries = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning("Failed to load KV cache from %s", self._path)
                self._entries = {}

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2) + "\n")

    def get(self, messages: list[dict]) -> CacheResult:
        """Look up a conversation in the cache.

        Returns CacheHit, CacheMiss, or CacheInvalid.
        """
        if not is_cacheable(messages):
            return CacheInvalid(reason="message sequence not cacheable")

        cache_id = conversation_hash(messages)
        entry = self._entries.get(cache_id)

        if entry is not None:
            return CacheHit(
                cache_id=cache_id,
                time_created=entry["time_created"],
                time_accessed=entry["time_accessed"],
                last_slot_id=entry.get("last_slot_id"),
            )

        return CacheMiss(cache_id=cache_id)

    def record_save(self, cache_id: str, slot_id: int) -> None:
        """Record that a conversation was saved to a slot file."""
        now = time.time()
        self._entries[cache_id] = {
            "time_created": now,
            "time_accessed": now,
            "last_slot_id": slot_id,
        }
        self._save()

    def record_restore(self, cache_id: str, slot_id: int) -> None:
        """Record that a cached conversation was restored (touch accessed time)."""
        entry = self._entries.get(cache_id)
        if entry is not None:
            entry["time_accessed"] = time.time()
            entry["last_slot_id"] = slot_id
            self._save()


# ---------------------------------------------------------------------------
# KVCacheProvider — singleton per slot_save_path
# ---------------------------------------------------------------------------


class KVCacheProvider:
    """Returns a shared KVCache instance for each slot save directory."""

    _instances: dict[str, KVCache] = {}

    @classmethod
    def get(cls, slot_save_dir: Path) -> KVCache:
        key = str(slot_save_dir)
        if key not in cls._instances:
            cls._instances[key] = KVCache(slot_save_dir)
        return cls._instances[key]
