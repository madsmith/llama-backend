"""Registry of active proxy requests, keyed by (model_index, slot_id).

Each entry holds an asyncio.Event that, when set, signals the streaming
generator to abort and return an error to the gateway client.
"""

from __future__ import annotations

import asyncio
import threading


class ActiveRequestManager:
    _lock = threading.Lock()
    _active: dict[tuple[int, int], asyncio.Event] = {}

    @classmethod
    def register(cls, model_index: int, slot_id: int) -> asyncio.Event:
        """Create and store a cancel event for this model+slot. Returns the event."""
        event = asyncio.Event()
        with cls._lock:
            cls._active[(model_index, slot_id)] = event
        return event

    @classmethod
    def unregister(cls, model_index: int, slot_id: int) -> None:
        """Remove the cancel event for this model+slot."""
        with cls._lock:
            cls._active.pop((model_index, slot_id), None)

    @classmethod
    def cancel(cls, model_index: int, slot_id: int) -> bool:
        """Set the cancel event. Returns True if a matching request was found."""
        with cls._lock:
            event = cls._active.get((model_index, slot_id))
        if event is None:
            return False
        event.set()
        return True

    @classmethod
    def list_cancellable(cls, model_index: int) -> list[int]:
        """Return slot IDs with active cancellable requests for the given model."""
        with cls._lock:
            return [sid for (mi, sid) in cls._active if mi == model_index]
