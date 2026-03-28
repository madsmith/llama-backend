from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Protocol

import httpx

from llama_manager.event_bus import EventBus
from llama_manager.llama_client import LlamaClient
from llama_manager.local_managed_model import LocalManagedModel
from llama_manager.remote_manager_client import RemoteModelProxy
from llama_manager.proxy.subscription import proxy_log



logger = logging.getLogger(__name__)

SubscriptionHandle = int


class LocalModelProvider(Protocol):
    def get_local_models(self) -> dict[str, LocalManagedModel]: ...
    def get_remote_models(self) -> list[RemoteModelProxy]: ...
    def get_client(self, model_suid: str) -> LlamaClient | None: ...
    def get_client_at(self, base_url: str) -> LlamaClient: ...


class SlotStatusService:
    """Owns slot state for all models: live-fetch, cache, and background polling."""

    def __init__(self, provider: LocalModelProvider, event_bus: EventBus) -> None:
        self._active_until: dict[int, float] = {}
        self._provider = provider
        self._event_bus = event_bus
        self._cache: dict[str, list[dict]] = {}
        self._subscriptions: dict[SubscriptionHandle, tuple[str, Callable[[list[dict]], None]]] = {}
        self._next_handle: SubscriptionHandle = 0
        self._task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None

    def mark_active(self, model_index: int, duration_ms: float = 2000) -> None:
        """Signal that model_index just received a request; boost poll rate for duration_ms."""
        self._active_until[model_index] = time.monotonic() + duration_ms / 1000

    def is_active(self, model_index: int) -> bool:
        return time.monotonic() < self._active_until.get(model_index, 0.0)

    # ------------------------------------------------------------------
    # Public query interface
    # ------------------------------------------------------------------

    async def get_slots(self, server_id: str, *, read_cache: bool = True) -> list[dict] | None:
        """Return slot info for *server_id*.

        Returns cached results by default.  Pass ``read_cache=False`` to
        bypass the cache and fetch live from the server.
        """
        if read_cache and server_id in self._cache:
            return self._cache[server_id]
        return await self._fetch(server_id)

    # ------------------------------------------------------------------
    # Subscription interface
    # ------------------------------------------------------------------

    def subscribe(
        self,
        server_id: str,
        callback: Callable[[list[dict]], None],
    ) -> SubscriptionHandle:
        """Register *callback* to be called whenever slots change for *server_id*.

        Returns a handle that can be passed to :meth:`unsubscribe`.
        """
        handle = self._next_handle
        self._next_handle += 1
        self._subscriptions[handle] = (server_id, callback)
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

    async def _fetch(self, server_id: str) -> list[dict] | None:
        for key, local_model in self._provider.get_local_models().items():
            if local_model.get_server_identifier() == server_id:
                client = self._provider.get_client(key)
                slots = await client.get_slots() if client is not None else None
                if slots is not None:
                    self._cache[server_id] = slots
                return slots

        for proxy in self._provider.get_remote_models():
            if proxy.server_id == server_id:
                # Slots arrive pushed via event bus (_event_loop keeps _cache current).
                # Fall back to a live fetch from the remote llama server if the port is known,
                # otherwise return the proxy's own stale cache.
                if server_id in self._cache:
                    return self._cache[server_id]
                if proxy.llama_server_port is not None:
                    base_url = f"http://{proxy._client.config.host}:{proxy.llama_server_port}"
                    slots = await self._provider.get_client_at(base_url).get_slots()
                    if slots is not None:
                        self._cache[server_id] = slots
                        return slots
                return await proxy.get_slots()

        return None

    def _notify(self, server_id: str, slots: list[dict]) -> None:
        for sub_server_id, callback in list(self._subscriptions.values()):
            if sub_server_id == server_id:
                callback(slots)

    async def _event_loop(self) -> None:
        """Listen for slot events pushed via the event bus (remote models)."""
        q = self._event_bus.subscribe("slots")
        try:
            while True:
                event = await q.get()
                server_id = event.get("server_id")
                slots = event.get("slots")
                if not server_id or slots is None:
                    continue
                # Only handle remote model events — local models are updated by _poll_loop
                remote_models = self._provider.get_remote_models()
                if any(m.server_id == server_id for m in remote_models):
                    self._cache[server_id] = slots
                    self._notify(server_id, slots)
        finally:
            self._event_bus.unsubscribe(q)

    async def _poll_loop(self) -> None:
        # Per-server scheduling state
        next_poll: dict[int, float] = {}   # model index → monotonic time of next fetch
        last_slots: dict[int, list[dict] | None] = {}  # model index → last known slots

        while True:
            now = time.monotonic()
            local_models = self._provider.get_local_models()

            # Initialise scheduling state for newly-seen managers
            for i_str in local_models:
                i = int(i_str)
                if i not in next_poll:
                    next_poll[i] = now
                    last_slots[i] = None

            # Poll every server that is due
            for i_str, local_model in local_models.items():
                i = int(i_str)
                if local_model.state.value != "running":
                    continue
                if now < next_poll.get(i, 0):
                    continue

                server_id = local_model.get_server_identifier()
                try:
                    client = self._provider.get_client(i_str)
                    slots = await client.get_slots() if client is not None else None
                except Exception:
                    next_poll[i] = time.monotonic() + 3.0
                    continue

                if slots is None:
                    next_poll[i] = time.monotonic() + 3.0
                    continue

                # Only publish and notify on actual change
                if slots != last_slots.get(i):
                    self._cache[server_id] = slots
                    self._event_bus.publish({
                        "type": "slots",
                        "server_id": server_id,
                        "slots": slots,
                    })
                    self._notify(server_id, slots)
                    last_slots[i] = slots

                active = any(s.get("is_processing") for s in slots) or self.is_active(i)
                next_poll[i] = time.monotonic() + (0.5 if active else 3.0)

            # Sleep only until the nearest due server.
            # Promote models flagged active via mark_active() to fast polling so
            # a newly-arrived request is picked up without waiting out a slow cycle.
            running = {
                int(i_str) for i_str, local_model in local_models.items()
                if local_model.state.value == "running"
            }
            for i in running:
                if self.is_active(i):
                    next_poll[i] = min(next_poll.get(i, now + 3.0), time.monotonic() + 0.5)
            due_times = [t for i, t in next_poll.items() if i in running]
            sleep_for = max(0.0, min(due_times) - time.monotonic()) if due_times else 3.0
            await asyncio.sleep(sleep_for)


# ------------------------------------------------------------------
# Low-level slot actions (used by KV-cache proxy logic)
# ------------------------------------------------------------------

async def slot_restore(backend: str, slot_id: int, filename: str) -> bool:
    """POST /slots/<id>?action=restore. Returns True on success."""
    # TODO - Remove
    logger.warning("KV cache: restoring slot %d from %s", slot_id, filename)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{backend}/slots/{slot_id}?action=restore",
                json={"filename": filename},
            )
            if resp.status_code == 200:
                # TODO - Remove
                logger.warning("KV cache: restore OK")
                proxy_log(f"KV cache restored slot {slot_id} from {filename}")
                return True
            # TODO - Remove
            logger.warning("KV cache: restore failed (%d)", resp.status_code)
            proxy_log(f"KV cache restore failed ({resp.status_code}): {resp.text}")
    except Exception as exc:
        # TODO - Remove
        logger.warning("KV cache: restore error: %s", exc)
        proxy_log(f"KV cache restore error: {exc}")
    return False


async def slot_save(backend: str, slot_id: int, filename: str) -> bool:
    """POST /slots/<id>?action=save. Returns True on success."""
    # TODO - Remove
    logger.warning("KV cache: saving slot %d as %s", slot_id, filename)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{backend}/slots/{slot_id}?action=save",
                json={"filename": filename},
            )
            if resp.status_code == 200:
                # TODO - Remove
                logger.warning("KV cache: save OK")
                proxy_log(f"KV cache saved slot {slot_id} as {filename}")
                return True
            # TODO - Remove
            logger.warning("KV cache: save failed (%d)", resp.status_code)
            proxy_log(f"KV cache save failed ({resp.status_code}): {resp.text}")
    except Exception as exc:
        # TODO - Remove
        logger.warning("KV cache: save error: %s", exc)
        proxy_log(f"KV cache save error: {exc}")
    return False
