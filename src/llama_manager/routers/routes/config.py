from __future__ import annotations

import secrets

from fastapi import Request

from llama_manager.config import AppConfig, load_config, save_config
from llama_manager.remote_manager_client import RemoteManagerClient
from llama_manager.process_manager import ProcessManager
from llama_manager.proxy import set_process_managers
from llama_manager.event_bus import EventBus


class ConfigRoutes:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    def generate_uplink_token(self) -> str:
        return secrets.token_hex(32)


    async def get_config(self):
        return load_config().model_dump()


    async def generate_token(self):
        return {"token": self.generate_uplink_token()}


    async def put_config(self, config: AppConfig, request: Request):
        # Auto-generate uplink token when enabling for the first time
        if config.manager_uplink.enabled and not config.manager_uplink.token:
            config.manager_uplink.token = self.generate_uplink_token()

        save_config(config)

        # Sync local process managers list to match new model count and types
        pms: list[ProcessManager | None] = request.app.state.process_managers
        while len(pms) < len(config.models):
            idx = len(pms)
            model = config.models[idx]
            pms.append(None if model.type == "remote" else ProcessManager(idx, config, self.event_bus))
            
        # Update type for existing indices (local<->remote switch)
        for i, model in enumerate(config.models):
            if i >= len(pms):
                break
            if model.type == "remote" and pms[i] is not None:
                pm = pms[i]
                if pm is not None and pm.state.value == "stopped":
                    pms[i] = None
            elif model.type != "remote" and pms[i] is None:
                pms[i] = ProcessManager(i, config, self.event_bus)
        # Shrink if models were removed (only trim stopped managers from the end)
        while len(pms) > len(config.models):
            pm = pms[-1]
            if pm is not None and pm.state.value != "stopped":
                break
            pms.pop()

        set_process_managers(pms)

        # Sync remote manager clients
        await self._sync_remote_managers(config, request, self.event_bus)

        return config.model_dump()


    async def _sync_remote_managers(self, config: AppConfig, request: Request, event_bus: EventBus) -> None:

        clients: list[RemoteManagerClient] = getattr(
            request.app.state, "remote_manager_clients", []
        )

        # Stop and remove clients beyond the new list length, or whose config changed
        new_clients: list[RemoteManagerClient] = []
        for i, remote_config in enumerate(config.remote_managers):
            if i < len(clients):
                existing = clients[i]
                # Restart if URL or token changed
                if existing.config.host != remote_config.host or existing.config.port != remote_config.port or existing.config.token != remote_config.token:
                    await existing.stop()
                    if remote_config.enabled and remote_config.host:
                        client = RemoteManagerClient(i, remote_config, config, event_bus, request.app)
                        await client.start()
                        new_clients.append(client)
                    else:
                        new_clients.append(existing)  # keep as-is but stopped
                else:
                    # Update mutable fields without restart
                    existing.config = remote_config
                    new_clients.append(existing)
            else:
                # New entry
                if remote_config.enabled and remote_config.host:
                    client = RemoteManagerClient(i, remote_config, config, event_bus, request.app)
                    await client.start()
                    new_clients.append(client)

        # Stop clients for removed entries
        for i in range(len(config.remote_managers), len(clients)):
            await clients[i].stop()

        request.app.state.remote_manager_clients = new_clients
