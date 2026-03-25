"""
Module-level event bus for pushing server state to WebSocket clients.

Events are plain dicts with at minimum:
  type      – event kind (e.g. "slots", "health", "state")
  server_id – "{manager_id}:model-{index}", unique across federated managers
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        # Maps each queue to the set of event types it wants (None = all types).
        self._subscribers: dict[asyncio.Queue[dict], frozenset[str] | None] = {}

    def subscribe(self, types: str | list[str] | None = None) -> asyncio.Queue[dict]:
        """Return a new queue that receives only events whose type is in *types*.

        Pass a single type string, a list of type strings, or ``None`` (default)
        to receive every event regardless of type.
        """
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        if types is None:
            self._subscribers[q] = None
        elif isinstance(types, str):
            self._subscribers[q] = frozenset({types})
        else:
            self._subscribers[q] = frozenset(types)
        return q

    def unsubscribe(
        self,
        q: asyncio.Queue[dict],
        types: str | list[str] | None = None,
    ) -> None:
        """Remove a subscription.

        If *types* is omitted the queue is removed entirely.  If *types* is
        given, only those types are removed from the queue's filter; the queue
        remains subscribed to any remaining types and is only dropped when its
        filter becomes empty.
        """
        if types is None:
            self._subscribers.pop(q, None)
            return
        current = self._subscribers.get(q)
        if current is None:
            # subscribed to all types — cannot partially unsubscribe
            return
        remove = frozenset({types} if isinstance(types, str) else types)
        remaining = current - remove
        if remaining:
            self._subscribers[q] = remaining
        else:
            del self._subscribers[q]

    def publish(self, event: dict[str, Any]) -> None:
        if "type" not in event:
            logger.warning("Event missing 'type' field, dropping")
            return
        
        event_type = event.get("type")
        for q, types in list(self._subscribers.items()):
            if types is None or event_type in types:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # slow subscriber — drop rather than block
