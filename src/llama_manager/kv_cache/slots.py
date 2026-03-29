from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SlotAvailabilityProvider:
    """Returns a shared SlotAvailability per model suid."""

    _instances: dict[str, SlotAvailability] = {}

    @classmethod
    def get(cls, suid: str, num_slots: int) -> SlotAvailability:
        if suid not in cls._instances:
            cls._instances[suid] = SlotAvailability(num_slots)
        return cls._instances[suid]


class SlotAvailability:
    """Thread/async-safe tracker of which slots are currently in use.

    Each slot also records the last cache_id it was used with, for potential
    future optimisations (sticky routing, fast-path restore skip).
    """

    def __init__(self, num_slots: int) -> None:
        self._lock = asyncio.Lock()
        self._in_use: set[int] = set()
        self._last_cache_id: dict[int, str | None] = {i: None for i in range(num_slots)}
        self._num_slots = num_slots

    async def get_available(self) -> int | None:
        """Reserve and return an available slot id, or None if all busy."""
        async with self._lock:
            for i in range(self._num_slots):
                if i not in self._in_use:
                    self._in_use.add(i)
                    logger.info("SlotAvailability: reserved slot %d", i)
                    return i
            
            logger.info("SlotAvailability: no slots available")
            return None

    async def free(self, slot_id: int, cache_id: str | None = None) -> None:
        """Release a slot, optionally recording the last cache_id it served."""
        async with self._lock:
            logger.info("SlotAvailability: freeing slot %d", slot_id)
            self._in_use.discard(slot_id)
            if cache_id is not None:
                self._last_cache_id[slot_id] = cache_id

    def last_cache_id(self, slot_id: int) -> str | None:
        """Return the last cache_id used on a slot (no lock needed for read)."""
        return self._last_cache_id.get(slot_id)
