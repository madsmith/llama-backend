"""Registry of active proxy requests, keyed by (model_index, slot_id).

Each entry holds an asyncio.Event that, when set, signals the streaming
generator to abort and return an error to the gateway client.
"""

from __future__ import annotations

import asyncio
import threading

_lock = threading.Lock()
_active: dict[tuple[int, int], asyncio.Event] = {}


def register(model_index: int, slot_id: int) -> asyncio.Event:
    """Create and store a cancel event for this model+slot. Returns the event."""
    event = asyncio.Event()
    with _lock:
        _active[(model_index, slot_id)] = event
    return event


def unregister(model_index: int, slot_id: int) -> None:
    """Remove the cancel event for this model+slot."""
    with _lock:
        _active.pop((model_index, slot_id), None)


def cancel(model_index: int, slot_id: int) -> bool:
    """Set the cancel event. Returns True if a matching request was found."""
    with _lock:
        event = _active.get((model_index, slot_id))
    if event is None:
        return False
    event.set()
    return True


def list_cancellable(model_index: int) -> list[int]:
    """Return slot IDs with active cancellable requests for the given model."""
    with _lock:
        return [sid for (mi, sid) in _active if mi == model_index]
