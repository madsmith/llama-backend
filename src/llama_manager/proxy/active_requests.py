"""Registry of active proxy requests, keyed by (suid, slot_id).

Each entry holds an asyncio.Event that, when set, signals the streaming
generator to abort and return an error to the gateway client.
"""

from __future__ import annotations

import asyncio
import threading


class ActiveRequestManager:
    _lock = threading.Lock()
    _active: dict[tuple[str, int], asyncio.Event] = {}

    @classmethod
    def register(cls, suid: str, slot_id: int) -> asyncio.Event:
        """Create and store a cancel event for this model+slot. Returns the event."""
        event = asyncio.Event()
        with cls._lock:
            cls._active[(suid, slot_id)] = event
        return event

    @classmethod
    def try_register(cls, suid: str, slot_id: int) -> asyncio.Event | None:
        """Atomically register only if the slot is not already claimed.

        Returns the new event, or None if another request already owns this slot.
        """
        with cls._lock:
            if (suid, slot_id) in cls._active:
                return None
            event = asyncio.Event()
            cls._active[(suid, slot_id)] = event
            return event

    @classmethod
    def unregister(cls, suid: str, slot_id: int) -> None:
        """Remove the cancel event for this model+slot."""
        with cls._lock:
            cls._active.pop((suid, slot_id), None)

    @classmethod
    def cancel(cls, suid: str, slot_id: int) -> bool:
        """Set the cancel event. Returns True if a matching request was found."""
        with cls._lock:
            event = cls._active.get((suid, slot_id))
        if event is None:
            return False
        event.set()
        return True

    @classmethod
    def list_cancellable(cls, suid: str) -> list[int]:
        """Return slot IDs with active cancellable requests for the given model."""
        with cls._lock:
            return [sid for (s, sid) in cls._active if s == suid]
