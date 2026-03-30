from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, NamedTuple

from llama_manager.util.event_bus import EventBus
from llama_manager.protocol.backend import LlamaManagerProtocol


logger = logging.getLogger(__name__)

SubscriptionHandle = int

type ModelSUID = str


class _PollTarget(NamedTuple):
    suid: ModelSUID
    is_local: bool


class SlotStatusService:
    """Owns slot state for all models: live-fetch, cache, and background polling."""

    def __init__(self, provider: LlamaManagerProtocol, event_bus: EventBus) -> None:
        self._active_until: dict[ModelSUID, float] = {}
        self._provider = provider
        self._event_bus = event_bus
        self._cache: dict[ModelSUID, list[dict]] = {}
        self._subscriptions: dict[SubscriptionHandle, tuple[ModelSUID, Callable[[list[dict]], None]]] = {}
        self._next_handle: SubscriptionHandle = 0
        self._task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None

    def mark_active(self, suid: ModelSUID, duration_ms: float = 2000) -> None:
        """Signal that suid just received a request; boost poll rate for duration_ms."""
        self._active_until[suid] = time.monotonic() + duration_ms / 1000

    def is_active(self, suid: ModelSUID) -> bool:
        return time.monotonic() < self._active_until.get(suid, 0.0)

    # ------------------------------------------------------------------
    # Public query interface
    # ------------------------------------------------------------------

    async def get_slots(self, suid: ModelSUID, *, read_cache: bool = True) -> list[dict] | None:
        """Return slot info for *suid*.

        Returns cached results by default.  Pass ``read_cache=False`` to
        bypass the cache and fetch live from the server.
        """
        if read_cache and suid in self._cache:
            return self._cache[suid]
        return await self._fetch(suid)

    # ------------------------------------------------------------------
    # Subscription interface
    # ------------------------------------------------------------------

    def subscribe(
        self,
        suid: ModelSUID,
        callback: Callable[[list[dict]], None],
    ) -> SubscriptionHandle:
        """Register *callback* to be called whenever slots change for *suid*.

        Returns a handle that can be passed to :meth:`unsubscribe`.
        """
        handle = self._next_handle
        self._next_handle += 1
        self._subscriptions[handle] = (suid, callback)
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        self._subscriptions.pop(handle, None)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())
        self._event_task = asyncio.create_task(self._event_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._event_task is not None:
            self._event_task.cancel()
            self._event_task = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch(self, suid: ModelSUID) -> list[dict] | None:
        local_model = self._provider.get_local_models().get(suid)
        if local_model is not None:
            client = self._provider.get_client(suid)
            slots = await client.get_slots() if client is not None else None
            if slots is not None:
                self._cache[suid] = slots
            return slots

        for proxy in self._provider.get_remote_models():
            if proxy.get_suid() == suid:
                # Slots arrive pushed via event bus (_event_loop keeps _cache current).
                # Fall back to a live fetch from the remote llama server if the port is known,
                # otherwise return the proxy's own stale cache.
                if suid in self._cache:
                    return self._cache[suid]
                if proxy.llama_server_port is not None:
                    base_url = f"http://{proxy._client.get_config().host}:{proxy.llama_server_port}"
                    slots = await self._provider.get_client_at(base_url).get_slots()
                    if slots is not None:
                        self._cache[suid] = slots
                        return slots
                return await proxy.get_slots()

        unmanaged = self._provider.get_remote_unmanaged().get(suid)
        if unmanaged is not None:
            slots = await unmanaged.get_slots()
            if slots is not None:
                self._cache[suid] = slots
            return slots

        return None

    def _notify(self, suid: ModelSUID, slots: list[dict]) -> None:
        for sub_suid, callback in list(self._subscriptions.values()):
            if sub_suid == suid:
                callback(slots)

    async def _event_loop(self) -> None:
        """Listen for slot events pushed via the event bus (remote models)."""
        q = self._event_bus.subscribe("slots")
        try:
            while True:
                event = await q.get()
                suid = event.get("id")
                slots = event.get("data", {}).get("slots")
                if not suid or slots is None:
                    continue
                # Only handle remote model events — local models are updated by _poll_loop
                remote_models = self._provider.get_remote_models()
                if any(m.get_suid() == suid for m in remote_models):
                    self._cache[suid] = slots
                    self._notify(suid, slots)
        finally:
            self._event_bus.unsubscribe(q)

    async def _poll_loop(self) -> None:
        next_poll: dict[ModelSUID, float] = {}
        last_slots: dict[ModelSUID, list[dict] | None] = {}

        while True:
            now = time.monotonic()
            local_models = self._provider.get_local_models()
            unmanaged_models = self._provider.get_remote_unmanaged()

            pollable: list[_PollTarget] = []
            for suid in local_models:
                if suid not in next_poll:
                    next_poll[suid] = now
                    last_slots[suid] = None
                pollable.append(_PollTarget(suid, True))

            for suid in unmanaged_models:
                if suid not in next_poll:
                    next_poll[suid] = now
                    last_slots[suid] = None
                pollable.append(_PollTarget(suid, False))

            for target in pollable:
                if target.is_local:
                    local_model = local_models.get(target.suid)
                    if local_model is None or local_model.state.value != "running":
                        continue
                if now < next_poll.get(target.suid, 0):
                    continue

                try:
                    if target.is_local:
                        client = self._provider.get_client(target.suid)
                        slots = await client.get_slots() if client is not None else None
                    else:
                        slots = await unmanaged_models[target.suid].get_slots()
                except Exception:
                    next_poll[target.suid] = time.monotonic() + 3.0
                    continue

                if slots is None:
                    next_poll[target.suid] = time.monotonic() + 3.0
                    continue

                # Only publish and notify on actual change
                if slots != last_slots.get(target.suid):
                    self._cache[target.suid] = slots
                    self._event_bus.publish({
                        "type": "slots",
                        "id": target.suid,
                        "data": {"slots": slots},
                    })
                    self._notify(target.suid, slots)
                    last_slots[target.suid] = slots

                has_active = any(s.get("is_processing") for s in slots) or self.is_active(target.suid)
                next_poll[target.suid] = time.monotonic() + (0.5 if has_active else 3.0)

            # Promote any server flagged active via mark_active() to fast polling
            for suid in list(self._active_until):
                if suid in next_poll and time.monotonic() < self._active_until.get(suid, 0.0):
                    next_poll[suid] = min(next_poll[suid], time.monotonic() + 0.5)

            running_suids = {
                suid for suid, lm in local_models.items() if lm.state.value == "running"
            } | set(unmanaged_models.keys())
            due_times = [t for suid, t in next_poll.items() if suid in running_suids]
            sleep_for = max(0.0, min(due_times) - time.monotonic()) if due_times else 3.0
            await asyncio.sleep(sleep_for)


# ------------------------------------------------------------------
# Low-level slot actions (used by KV-cache proxy logic)
# ------------------------------------------------------------------

