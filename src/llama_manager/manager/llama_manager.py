from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from llama_manager.config import AppConfig, ModelConfig, save_config
from llama_manager.dev import DevViteService
from llama_manager.kv_cache import resolve_slot_save_path
from llama_manager.protocol.backend import Backend, LlamaManagerProtocol
from llama_manager.proxy import ProxyServer, SlotStatusService
from llama_manager.util.event_bus import EventBus
from llama_manager.manager.remote_client import RemoteManagerClient
from llama_manager.manager.backends import LocalManagedModel, RemoteModelProxy, RemoteUnmanagedModel

from .llama_client import LlamaClient

logger = logging.getLogger(__name__)

type LocalModelIdentifier = str


class LlamaManager(LlamaManagerProtocol):
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
        self._ttl_task: asyncio.Task | None = None
        self._model_last_activity: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_local_models(self) -> dict[LocalModelIdentifier, LocalManagedModel]:
        return self._local_models

    def get_remote_models(self) -> list[RemoteModelProxy]:
        return [model for client in self.remote_manager_clients for model in client.models]

    def get_remote_unmanaged(self) -> dict[LocalModelIdentifier, RemoteUnmanagedModel]:
        return self._remote_unmanaged

    def get_manager_id(self) -> str:
        return self.config.manager_id

    def get_client(self, model_suid: str) -> LlamaClient | None:
        model: Backend | None = self._local_models.get(model_suid)
        if model is None:
            model = self._remote_unmanaged.get(model_suid)
        
        if model is None:
            return None

        return LlamaClient(model.get_base_url())

    def get_client_at(self, base_url: str) -> LlamaClient:
        return LlamaClient(base_url)

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def find_backend(self, model_id: str | None) -> Backend | None:
        """Find the backend that serves model_id, or the default backend if None."""
        # Remote proxies (uplink) first
        for proxy in self.get_remote_models():
            if model_id is None or model_id in proxy.get_model_ids():
                return proxy

        # Local and remote-unmanaged models
        if model_id is None:
            if self._local_models:
                return next(iter(self._local_models.values()))
            if self._remote_unmanaged:
                return next(iter(self._remote_unmanaged.values()))
            return None

        for m in self._local_models.values():
            if model_id in m.get_model_ids():
                return m
        for m in self._remote_unmanaged.values():
            if model_id in m.get_model_ids():
                return m
        return None

    def get_model_config(self, suid: str) -> ModelConfig | None:
        """Return the ModelConfig for a locally-configured model by suid."""
        for m in self.config.models:
            if m.suid == suid:
                return m
        return None

    # ------------------------------------------------------------------
    # JIT + TTL: server lifecycle APIs used by the proxy handler
    # ------------------------------------------------------------------

    def touch(self, suid: str) -> None:
        """Record activity for a model, resetting its TTL timer."""
        self._model_last_activity[suid] = time.monotonic()

    def get_slot_save_path(self, suid: str) -> Path | None:
        """Return the KV cache slot save directory for a model, or None."""
        m = self.get_model_config(suid)
        if m is None:
            return None
        adv = m.advanced
        if not adv.kv_cache:
            return None
        if adv.slot_save_path:
            return Path(adv.slot_save_path).expanduser().resolve()
        base = self.config.web_ui.slot_save_path or "./slot_saves"
        model_id = m.effective_id or suid
        return Path(base).expanduser().resolve() / model_id

    async def ensure_server(self, backend: Backend) -> None:
        """Start model server on-demand if JIT or TTL is enabled."""
        suid = backend.get_suid()
        m = self.get_model_config(suid)
        has_ttl = m is not None and m.model_ttl is not None
        if not self.config.api_server.jit_model_server and not has_ttl:
            return
        local_model = self._local_models.get(suid)
        if local_model is None:
            return  # remote model — no local process to start
        if local_model.state.value == "running":
            return
        if local_model.state.value not in ("stopped", "error"):
            return

        timeout = self.config.api_server.jit_timeout or 80
        name = m.name or suid if m else suid
        self.proxy.log(f"JIT: model server [{name}] is {local_model.state.value}, starting...")
        await local_model.start()

        elapsed = 0.0
        while elapsed < timeout:
            state = local_model.state.value
            if state == "running":
                self.proxy.log(f"JIT: model server [{name}] ready ({elapsed:.1f}s)")
                return
            if state == "error":
                raise RuntimeError(f"Model server [{name}] failed to start")
            await asyncio.sleep(0.5)
            elapsed += 0.5

        raise RuntimeError(f"Model server [{name}] did not become ready within {timeout}s")

    async def task_ttl_checker(self) -> None:
        """Background task that stops idle models whose TTL has expired."""
        while True:
            await asyncio.sleep(30)
            try:
                now = time.monotonic()
                for m in self.config.models:
                    if m.model_ttl is None or m.type == "remote":
                        continue
                    local_model = self._local_models.get(m.suid)
                    if local_model is None or local_model.state.value != "running":
                        continue
                    last = self._model_last_activity.get(m.suid)
                    if last is None:
                        continue
                    if now - last > m.model_ttl * 60:
                        name = m.name or m.suid
                        self.proxy.log(f"TTL expired for [{name}], stopping server")
                        await local_model.stop()
            except Exception:
                pass  # don't crash the background task

    # ------------------------------------------------------------------
    # Model construction helpers
    # ------------------------------------------------------------------

    def _make_local_model(self, idx: int, config: AppConfig) -> LocalManagedModel:
        """Construct a LocalManagedModel with all config pre-resolved."""
        model_config = config.models[idx]
        port = config.api_server.llama_server_starting_port + idx
        raw_path = model_config.advanced.llama_server_path or config.api_server.llama_server_path
        llama_server_path = Path(raw_path).expanduser() if raw_path else None
        slot_save_path = resolve_slot_save_path(config, idx)
        return LocalManagedModel(
            manager=self,
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
            if model_config.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(model_config, self)
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

        if vite is not None:
            await vite.start()

        await self.proxy.start()

        for model_config in self.config.models:
            local_model = self._local_models.get(model_config.suid)
            if model_config.auto_start and local_model is not None:
                logger.info("Auto-starting model %s", model_config.name or model_config.suid)
                await local_model.start()

        # _sync_remote_managers works for initial startup (empty list) and
        # incremental apply_config updates — no separate connect path needed.
        await self._sync_remote_managers(self.config)

        self._data_publisher_task = asyncio.create_task(self.data_publisher())
        self._ttl_task = asyncio.create_task(self.task_ttl_checker())
        await self.slot_status.start()

    async def _stop(self, vite: DevViteService | None) -> None:
        if self._data_publisher_task is not None:
            self._data_publisher_task.cancel()
            self._data_publisher_task = None

        if self._ttl_task is not None:
            self._ttl_task.cancel()
            self._ttl_task = None

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
            if model_config.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(model_config, self)
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
        """Publish health events for running local models and remote unmanaged models."""
        while True:
            try:
                for local_model in self._local_models.values():
                    if local_model.state.value != "running":
                        continue
                    try:
                        health = await LlamaClient(local_model.get_base_url()).get_health()
                        if health is not None:
                            self.event_bus.publish({
                                "type": "health",
                                "id": local_model.get_suid(),
                                "data": {"health": health},
                            })
                    except Exception:
                        logger.warning("data_publisher: health fetch failed for %s", local_model.get_suid(), exc_info=True)

                for unmanaged in self._remote_unmanaged.values():
                    try:
                        health = await LlamaClient(unmanaged.get_base_url()).get_health()
                        if health is not None:
                            self.event_bus.publish({
                                "type": "health",
                                "id": unmanaged.get_suid(),
                                "data": {"health": health},
                            })
                    except Exception:
                        logger.warning("data_publisher: health fetch failed for %s", unmanaged.get_suid(), exc_info=True)
            except Exception:
                logger.warning("data_publisher: unexpected error", exc_info=True)

            await asyncio.sleep(3.0)

    def generate_token(self) -> str:
        return secrets.token_hex(32)
