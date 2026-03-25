from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from .config import AppConfig
from .dev import DevViteService
from .event_bus import bus as event_bus
from .process_manager import ProcessManager
from .proxy import ProxyServer, set_process_managers, shutdown_proxy_subscribers
from .remote_manager_client import RemoteManagerClient, RemoteModelProxy

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class LlamaManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.proxy = ProxyServer(config)
        self.process_managers: list[ProcessManager | None] = []

    async def data_publisher(self) -> None:
        """Publish slot and health events for running local models to the event bus."""
        import httpx

        last_slots: dict[str, list[dict]] = {}

        while True:
            try:
                for pm in self.process_managers:
                    if pm is None or isinstance(pm, RemoteModelProxy):
                        continue
                    if pm.state.value != "running":
                        continue

                    if isinstance(pm, ProcessManager):
                        base = pm.get_server_address()
                        try:
                            async with httpx.AsyncClient(timeout=2) as client:
                                resp = await client.get(f"{base}/slots")
                                if resp.status_code == 200:
                                    slots = resp.json()
                                    last_slots[pm.get_server_identifier()] = slots
                                    event_bus.publish({"type": "slots", "server_id": pm.get_server_identifier(), "slots": slots})
                        except Exception:
                            pass
                        try:
                            async with httpx.AsyncClient(timeout=2) as client:
                                resp = await client.get(f"{base}/health")
                                if resp.status_code in (200, 503):
                                    event_bus.publish({"type": "health", "server_id": pm.get_server_identifier(), "health": resp.json()})
                        except Exception:
                            pass
                    else:
                        log.warning("Unknown process manager type: %s", type(pm))
            except Exception:
                pass

            active = any(
                s.get("is_processing")
                for slots in last_slots.values()
                for s in slots
            )
            await asyncio.sleep(0.5 if active else 3.0)

    def get_lifespan(self, vite: DevViteService | None = None):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            self.process_managers = [
                None if m.type == "remote" else ProcessManager(idx, self.config)
                for idx, m in enumerate(self.config.models)
            ]
            app.state.process_managers = self.process_managers
            app.state.remote_manager_clients = []
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
                    client = RemoteManagerClient(i, remote_config, self.config, app)
                    app.state.remote_manager_clients.append(client)
                    await client.start()

            data_publisher_task = asyncio.create_task(self.data_publisher())

            yield

            # === Teardown ===
            data_publisher_task.cancel()

            for client in app.state.remote_manager_clients:
                await client.stop()

            await self.proxy.stop()
            shutdown_proxy_subscribers()

            for pm in self.process_managers:
                if pm is not None:
                    pm.shutdown_subscribers()
                    await pm.stop()

            if vite is not None:
                await vite.stop()

        return lifespan
