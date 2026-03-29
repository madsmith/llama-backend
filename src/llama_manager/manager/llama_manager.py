from __future__ import annotations

import asyncio
import logging
import httpx
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from llama_manager.config import AppConfig, save_config
from llama_manager.dev import DevViteService
from llama_manager.event_bus import EventBus
from llama_manager.kv_cache import resolve_slot_save_path
from llama_manager.llama_client import LlamaClient
from llama_manager.proxy import ProxyServer, SlotStatusService, set_llama_manager
from llama_manager.manager.remote_client import RemoteManagerClient
from llama_manager.manager.backends import LocalManagedModel, RemoteModelProxy, RemoteUnmanagedModel

log = logging.getLogger(__name__)

type LocalModelIdentifier = str


class LlamaManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.event_bus = EventBus()
        self._local_models: dict[LocalModelIdentifier, LocalManagedModel] = {}
        self._remote_unmanaged: dict[LocalModelIdentifier, RemoteUnmanagedModel] = {}
        self.remote_manager_clients: list[RemoteManagerClient] = []
        self.uplink_client_count: int = 0
        self.slot_status = SlotStatusService(self, self.event_bus)
        self.proxy = ProxyServer(self)
        self._data_publisher_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_local_models(self) -> dict[LocalModelIdentifier, LocalManagedModel]:
        return self._local_models

    def get_remote_models(self) -> list[RemoteModelProxy]:
        return [model for client in self.remote_manager_clients for model in client.models]

    def get_remote_unmanaged(self) -> dict[LocalModelIdentifier, RemoteUnmanagedModel]:
        return self._remote_unmanaged

    def get_client(self, model_suid: str) -> LlamaClient | None:
        local_model = self._local_models.get(model_suid)
        if local_model is None:
            return None
        return LlamaClient(local_model.get_base_url())

    def get_client_at(self, base_url: str) -> LlamaClient:
        return LlamaClient(base_url)

    # ------------------------------------------------------------------
    # Model construction helpers
    # ------------------------------------------------------------------

    def _make_local_model(self, idx: int, config: AppConfig) -> LocalManagedModel:
        """Construct a LocalManagedModel with all config pre-resolved."""
        model_config = config.models[idx]
        server_id = f"{config.manager_id}:{model_config.suid}"
        port = config.api_server.llama_server_starting_port + idx
        raw_path = model_config.advanced.llama_server_path or config.api_server.llama_server_path
        llama_server_path = Path(raw_path).expanduser() if raw_path else None
        slot_save_path = resolve_slot_save_path(config, idx)
        return LocalManagedModel(
            server_id=server_id,
            model_config=model_config,
            port=port,
            event_bus=self.event_bus,
            log_buffer_size=config.web_ui.log_buffer_size,
            llama_server_path=llama_server_path,
            slot_save_path=slot_save_path,
        )

    def _update_local_model(self, local_model: LocalManagedModel, idx: int, config: AppConfig) -> None:
        """Push refreshed config to an existing LocalManagedModel."""
        model_config = config.models[idx]
        raw_path = model_config.advanced.llama_server_path or config.api_server.llama_server_path
        llama_server_path = Path(raw_path).expanduser() if raw_path else None
        slot_save_path = resolve_slot_save_path(config, idx)
        local_model.update_config(model_config, llama_server_path, slot_save_path)

    # ------------------------------------------------------------------
    # Model initialisation
    # ------------------------------------------------------------------

    def _initialize_models(self, config: AppConfig) -> None:
        """Build _local_models and _remote_unmanaged fresh from config."""
        local_models: dict[LocalModelIdentifier, LocalManagedModel] = {}
        unmanaged: dict[LocalModelIdentifier, RemoteUnmanagedModel] = {}
        for idx, model_config in enumerate(config.models):
            key = model_config.suid
            server_id = f"{config.manager_id}:{model_config.suid}"
            if model_config.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(model_config, server_id=server_id)
            else:
                local_models[key] = self._make_local_model(idx, config)
        self._local_models = local_models
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

        for model_config in self.config.models:
            local_model = self._local_models.get(model_config.suid)
            if model_config.auto_start and local_model is not None:
                log.info("Auto-starting model %s", model_config.name or idx)
                await local_model.start()

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

        for local_model in self._local_models.values():
            await local_model.stop()

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
        """Save config and incrementally sync local models and remote clients."""
        if config.manager_uplink.enabled and not config.manager_uplink.token:
            config.manager_uplink.token = self.generate_token()

        save_config(config)
        self.config = config

        local_models = self._local_models
        unmanaged = self._remote_unmanaged

        # Sync all configured models (handles new additions, type changes, config updates)
        for idx, model_config in enumerate(config.models):
            key = model_config.suid
            server_id = f"{config.manager_id}:{model_config.suid}"
            if model_config.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(model_config, server_id=server_id)
                existing = local_models.get(key)
                if existing is not None and existing.state.value == "stopped":
                    del local_models[key]
            else:
                unmanaged.pop(key, None)
                if key in local_models:
                    self._update_local_model(local_models[key], idx, config)
                else:
                    local_models[key] = self._make_local_model(idx, config)

        # Shrink: stop and remove entries for models no longer in config
        valid_suids = {m.suid for m in config.models}
        orphaned_keys = [k for k in local_models if k not in valid_suids]
        for key in orphaned_keys:
            local_model = local_models[key]
            if local_model.state.value not in ("stopped", "error"):
                await local_model.stop()
            del local_models[key]
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
                existing_config = existing.get_config()
                if (existing_config.host != remote_config.host
                        or existing_config.port != remote_config.port
                        or existing_config.token != remote_config.token):
                    await existing.stop()
                    if remote_config.enabled and remote_config.host:
                        client = RemoteManagerClient(i, remote_config, config, self.event_bus)
                        await client.start()
                        new_clients.append(client)
                    else:
                        new_clients.append(existing)
                else:
                    existing.set_config(remote_config)
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
                for local_model in self._local_models.values():
                    if local_model.state.value != "running":
                        continue
                    try:
                        async with httpx.AsyncClient(timeout=2) as client:
                            resp = await client.get(f"{local_model.get_base_url()}/health")
                            if resp.status_code in (200, 503):
                                self.event_bus.publish({
                                    "type": "health",
                                    "server_id": local_model.get_server_identifier(),
                                    "health": resp.json(),
                                })
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(3.0)

    def generate_token(self) -> str:
        return secrets.token_hex(32)
