from __future__ import annotations

import asyncio
import logging
import httpx
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import AppConfig, save_config
from .dev import DevViteService
from .event_bus import EventBus
from .process_manager import ProcessManager
from .proxy import ProxyServer, set_llama_manager
from .proxy.slots import SlotStatusService
from .remote_manager_client import RemoteManagerClient, RemoteModelProxy
from .remote_unmanaged import RemoteUnmanagedModel

log = logging.getLogger(__name__)

type LocalModelIdentifier = str

class LlamaManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.event_bus = EventBus()
        self._process_managers: dict[LocalModelIdentifier, ProcessManager] = {}
        self._remote_unmanaged: dict[LocalModelIdentifier, RemoteUnmanagedModel] = {}
        self.remote_manager_clients: list[RemoteManagerClient] = []
        self.uplink_client_count: int = 0
        self.slot_status = SlotStatusService(self, self.event_bus)
        self.proxy = ProxyServer(self)
        self._data_publisher_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_process_managers(self) -> dict[LocalModelIdentifier, ProcessManager]:
        return self._process_managers

    def get_remote_models(self) -> list[RemoteModelProxy]:
        return [model for client in self.remote_manager_clients for model in client.models]

    def get_remote_unmanaged(self) -> dict[LocalModelIdentifier, RemoteUnmanagedModel]:
        return self._remote_unmanaged

    # ------------------------------------------------------------------
    # Model initialisation
    # ------------------------------------------------------------------

    def _initialize_models(self, config: AppConfig) -> None:
        """Build process_managers and _remote_unmanaged fresh from config.

        This is the single point where ProcessManager and RemoteUnmanagedModel
        instances are constructed for a clean (re)start.  apply_config handles
        the incremental update case separately but defers to this for new slots.
        """
        pms: dict[LocalModelIdentifier, ProcessManager] = {}
        unmanaged: dict[LocalModelIdentifier, RemoteUnmanagedModel] = {}
        for idx, model in enumerate(config.models):
            key = str(idx)
            if model.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(idx, config.manager_id, model)
            else:
                pms[key] = ProcessManager(idx, config, self.event_bus)
        self._process_managers = pms
        self._remote_unmanaged = unmanaged

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _start(self, vite: DevViteService | None) -> None:
        self._initialize_models(self.config)
        self.remote_manager_clients = []
        set_llama_manager(self)

        if vite is not None:
            await vite.start()

        await self.proxy.start()

        for i, m in enumerate(self.config.models):
            pm = self._process_managers.get(str(i))
            if m.auto_start and pm is not None:
                log.info("Auto-starting model %s", m.name or i)
                await pm.start()

        # _sync_remote_managers works for initial startup (empty list) and
        # incremental apply_config updates — no separate connect path needed.
        await self._sync_remote_managers(self.config)

        self._data_publisher_task = asyncio.create_task(self.data_publisher())
        await self.slot_status.start()

    async def _stop(self, vite: DevViteService | None) -> None:
        if self._data_publisher_task is not None:
            self._data_publisher_task.cancel()
            self._data_publisher_task = None

        await self.slot_status.stop()

        for client in self.remote_manager_clients:
            await client.stop()

        await self.proxy.stop()

        for pm in self._process_managers.values():
            await pm.stop()

        if vite is not None:
            await vite.stop()

    def get_lifespan(self, vite: DevViteService | None = None):
        @asynccontextmanager
        async def lifespan(_app: FastAPI):
            await self._start(vite)
            try:
                yield
            finally:
                await self._stop(vite)

        return lifespan

    # ------------------------------------------------------------------
    # Config application (incremental update)
    # ------------------------------------------------------------------

    async def apply_config(self, config: AppConfig) -> AppConfig:
        """Save config and incrementally sync process managers and remote clients."""
        if config.manager_uplink.enabled and not config.manager_uplink.token:
            config.manager_uplink.token = self.generate_token()

        save_config(config)
        self.config = config

        process_managers = self._process_managers
        unmanaged = self._remote_unmanaged

        # Sync all configured models (handles new additions and type changes)
        for idx, model in enumerate(config.models):
            key = str(idx)
            if model.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(idx, config.manager_id, model)
                process_manager = process_managers.get(key)
                if process_manager is not None and process_manager.state.value == "stopped":
                    del process_managers[key]
            else:
                unmanaged.pop(key, None)
                if key not in process_managers:
                    process_managers[key] = ProcessManager(idx, config, self.event_bus)

        # Shrink: remove stopped entries for slots no longer configured
        orphaned_keys = sorted(
            (k for k in process_managers if int(k) >= len(config.models)),
            key=int,
            reverse=True,
        )
        for key in orphaned_keys:
            if process_managers[key].state.value != "stopped":
                break
            del process_managers[key]
            unmanaged.pop(key, None)

        set_llama_manager(self)
        await self._sync_remote_managers(config)
        return config

    # ------------------------------------------------------------------
    # Remote manager client sync
    # ------------------------------------------------------------------

    async def _sync_remote_managers(self, config: AppConfig) -> None:
        clients = self.remote_manager_clients
        new_clients: list[RemoteManagerClient] = []
        for i, remote_config in enumerate(config.remote_managers):
            if i < len(clients):
                existing = clients[i]
                if (existing.config.host != remote_config.host
                        or existing.config.port != remote_config.port
                        or existing.config.token != remote_config.token):
                    await existing.stop()
                    if remote_config.enabled and remote_config.host:
                        client = RemoteManagerClient(i, remote_config, config, self.event_bus)
                        await client.start()
                        new_clients.append(client)
                    else:
                        new_clients.append(existing)
                else:
                    existing.config = remote_config
                    new_clients.append(existing)
            else:
                if remote_config.enabled and remote_config.host:
                    client = RemoteManagerClient(i, remote_config, config, self.event_bus)
                    await client.start()
                    new_clients.append(client)

        for i in range(len(config.remote_managers), len(clients)):
            await clients[i].stop()

        self.remote_manager_clients = new_clients

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def data_publisher(self) -> None:
        """Publish health events for running local models to the event bus."""

        while True:
            try:
                for pm in self._process_managers.values():
                    if pm.state.value != "running":
                        continue
                    base = pm.get_server_address()
                    try:
                        async with httpx.AsyncClient(timeout=2) as client:
                            resp = await client.get(f"{base}/health")
                            if resp.status_code in (200, 503):
                                self.event_bus.publish({
                                    "type": "health",
                                    "server_id": pm.get_server_identifier(),
                                    "health": resp.json(),
                                })
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(3.0)

    def generate_token(self) -> str:
        return secrets.token_hex(32)
