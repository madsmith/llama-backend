from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import httpx

from llama_manager.event_bus import EventBus
from llama_manager.llama_client import LlamaClient
from llama_manager.process_manager import ProcessManager
from llama_manager.remote_manager_client import RemoteModelProxy
from llama_manager.proxy.subscription import proxy_log

logger = logging.getLogger(__name__)


class ProcessManagerProvider(Protocol):
    def get_process_managers(self) -> list[ProcessManager | None]: ...


class SlotStatusService:
    """Owns slot state for all models: live-fetch, cache, and background polling."""

    def __init__(self, provider: ProcessManagerProvider, event_bus: EventBus) -> None:
        self._provider = provider
        self._event_bus = event_bus
        self._cache: dict[int, list[dict]] = {}
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public query interface
    # ------------------------------------------------------------------

    async def get_slots(self, model: int, *, read_cache: bool = True) -> list[dict] | None:
        """Return slot info for *model*.

        Returns cached results by default.  Pass ``read_cache=False`` to
        bypass the cache and fetch live from the server.
        """
        if read_cache and model in self._cache:
            return self._cache[model]
        return await self._fetch(model)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch(self, model: int) -> list[dict] | None:
        pms = self._provider.get_process_managers()
        if model < 0 or model >= len(pms):
            return None
        pm = pms[model]
        if pm is None:
            return None

        if isinstance(pm, RemoteModelProxy):
            cfg = pm._client.config
            url = f"http://{cfg.host}:{cfg.port}/api/status/slots?model={pm.remote_model_index}"
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        slots = resp.json()
                        pm.set_slots(slots)
                        self._cache[model] = slots
                        return slots
            except Exception:
                pass
            return pm.get_cached_slots()

        if not isinstance(pm, ProcessManager):
            return None

        slots = await LlamaClient(model).get_slots()
        if slots is not None:
            self._cache[model] = slots
        return slots

    async def _poll_loop(self) -> None:
        while True:
            any_active = False
            for i, pm in enumerate(self._provider.get_process_managers()):
                if not isinstance(pm, ProcessManager):
                    continue
                if pm.state.value != "running":
                    continue
                try:
                    slots = await LlamaClient(i).get_slots()
                    if slots is not None:
                        self._cache[i] = slots
                        self._event_bus.publish({
                            "type": "slots",
                            "server_id": pm.get_server_identifier(),
                            "slots": slots,
                        })
                        if any(s.get("is_processing") for s in slots):
                            any_active = True
                except Exception:
                    pass
            await asyncio.sleep(0.5 if any_active else 3.0)


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
