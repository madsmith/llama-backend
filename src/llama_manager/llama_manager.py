from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import AppConfig, save_config
from .dev import DevViteService
from .event_bus import EventBus
from .process_manager import ProcessManager
from .proxy import ProxyServer, set_process_managers, shutdown_proxy_subscribers
from .proxy.slots import SlotStatusService
from .remote_manager_client import RemoteManagerClient

log = logging.getLogger(__name__)


class LlamaManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.event_bus = EventBus()
        self.process_managers: list[ProcessManager | None] = []
        self.remote_manager_clients: list[RemoteManagerClient] = []
        self.app: FastAPI | None = None
        self.slot_status = SlotStatusService(self, self.event_bus)
        self.proxy = ProxyServer(self)

    def get_process_managers(self) -> list[ProcessManager | None]:
        return self.process_managers

    async def data_publisher(self) -> None:
        """Publish health events for running local models to the event bus."""
        import httpx

        while True:
            try:
                for pm in self.process_managers:
                    if not isinstance(pm, ProcessManager):
                        continue
                    if pm.state.value != "running":
                        continue
                    base = pm.get_server_address()
                    try:
                        async with httpx.AsyncClient(timeout=2) as client:
                            resp = await client.get(f"{base}/health")
                            if resp.status_code in (200, 503):
                                self.event_bus.publish({"type": "health", "server_id": pm.get_server_identifier(), "health": resp.json()})
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(3.0)

    def generate_token(self) -> str:
        return secrets.token_hex(32)

    async def apply_config(self, config: AppConfig) -> AppConfig:
        """Save config and sync process managers and remote manager clients."""
        if config.manager_uplink.enabled and not config.manager_uplink.token:
            config.manager_uplink.token = self.generate_token()

        save_config(config)

        pms = self.process_managers
        while len(pms) < len(config.models):
            idx = len(pms)
            model = config.models[idx]
            pms.append(None if model.type == "remote" else ProcessManager(idx, config, self.event_bus))

        for i, model in enumerate(config.models):
            if i >= len(pms):
                break
            if model.type == "remote" and pms[i] is not None:
                pm = pms[i]
                if pm is not None and pm.state.value == "stopped":
                    pms[i] = None
            elif model.type != "remote" and pms[i] is None:
                pms[i] = ProcessManager(i, config, self.event_bus)

        while len(pms) > len(config.models):
            pm = pms[-1]
            if pm is not None and pm.state.value != "stopped":
                break
            pms.pop()

        set_process_managers(pms)
        await self._sync_remote_managers(config)
        return config

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
                        client = RemoteManagerClient(i, remote_config, config, self.event_bus, self.app)
                        await client.start()
                        new_clients.append(client)
                    else:
                        new_clients.append(existing)
                else:
                    existing.config = remote_config
                    new_clients.append(existing)
            else:
                if remote_config.enabled and remote_config.host:
                    client = RemoteManagerClient(i, remote_config, config, self.event_bus, self.app)
                    await client.start()
                    new_clients.append(client)

        for i in range(len(config.remote_managers), len(clients)):
            await clients[i].stop()

        self.remote_manager_clients = new_clients
        if self.app is not None:
            self.app.state.remote_manager_clients = new_clients

    def get_lifespan(self, vite: DevViteService | None = None):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            self.app = app
            self.process_managers = [
                None if m.type == "remote" else ProcessManager(idx, self.config, self.event_bus)
                for idx, m in enumerate(self.config.models)
            ]
            app.state.process_managers = self.process_managers
            self.remote_manager_clients = []
            app.state.remote_manager_clients = self.remote_manager_clients
            app.state.uplink_client_count = 0
            set_process_managers(self.process_managers)

            if vite is not None:
                await vite.start()

            await self.proxy.start()

            for i, m in enumerate(self.config.models):
                pm = self.process_managers[i]
                if m.auto_start and pm is not None:
                    print(f"[auto-start] Starting model {m.name or i} ...")
                    await pm.start()

            for i, remote_config in enumerate(self.config.remote_managers):
                if remote_config.enabled and remote_config.host:
                    client = RemoteManagerClient(i, remote_config, self.config, self.event_bus, app)
                    self.remote_manager_clients.append(client)
                    await client.start()

            data_publisher_task = asyncio.create_task(self.data_publisher())
            await self.slot_status.start()

            yield

            # === Teardown ===
            data_publisher_task.cancel()
            await self.slot_status.stop()

            for client in self.remote_manager_clients:
                await client.stop()

            await self.proxy.stop()
            shutdown_proxy_subscribers()

            for pm in self.process_managers:
                if pm is not None:
                    await pm.stop()

            if vite is not None:
                await vite.stop()

        return lifespan
