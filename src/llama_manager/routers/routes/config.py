from __future__ import annotations

import secrets

from fastapi import Request

from ...config import AppConfig, load_config, save_config
from ...process_manager import ProcessManager


def generate_uplink_token() -> str:
    return secrets.token_hex(32)


async def get_config():
    return load_config().model_dump()


async def generate_token():
    return {"token": generate_uplink_token()}


async def put_config(cfg: AppConfig, request: Request):
    # Auto-generate uplink token when enabling for the first time
    if cfg.manager_uplink.enabled and not cfg.manager_uplink.token:
        cfg.manager_uplink.token = generate_uplink_token()

    save_config(cfg)

    # Sync local process managers list to match new model count and types
    pms: list[ProcessManager | None] = request.app.state.process_managers
    while len(pms) < len(cfg.models):
        idx = len(pms)
        m = cfg.models[idx]
        pms.append(None if m.type == "remote" else ProcessManager(idx, cfg))
    # Update type for existing indices (local<->remote switch)
    for i, m in enumerate(cfg.models):
        if i >= len(pms):
            break
        if m.type == "remote" and pms[i] is not None:
            pm = pms[i]
            if pm is not None and pm.state.value == "stopped":
                pms[i] = None
        elif m.type != "remote" and pms[i] is None:
            pms[i] = ProcessManager(i, cfg)
    # Shrink if models were removed (only trim stopped managers from the end)
    while len(pms) > len(cfg.models):
        pm = pms[-1]
        if pm is not None and pm.state.value != "stopped":
            break
        pms.pop()

    from ...proxy import set_process_managers
    set_process_managers(pms)

    # Sync remote manager clients
    await _sync_remote_managers(cfg, request)

    return cfg.model_dump()


async def _sync_remote_managers(cfg: AppConfig, request: Request) -> None:
    from ...remote_manager_client import RemoteManagerClient

    clients: list[RemoteManagerClient] = getattr(
        request.app.state, "remote_manager_clients", []
    )

    # Stop and remove clients beyond the new list length, or whose config changed
    new_clients: list[RemoteManagerClient] = []
    for i, rm_cfg in enumerate(cfg.remote_managers):
        if i < len(clients):
            existing = clients[i]
            # Restart if URL or token changed
            if existing.cfg.url != rm_cfg.url or existing.cfg.token != rm_cfg.token:
                await existing.stop()
                if rm_cfg.enabled and rm_cfg.url:
                    client = RemoteManagerClient(i, rm_cfg, request.app)
                    await client.start()
                    new_clients.append(client)
                else:
                    new_clients.append(existing)  # keep as-is but stopped
            else:
                # Update mutable fields without restart
                existing.cfg = rm_cfg
                new_clients.append(existing)
        else:
            # New entry
            if rm_cfg.enabled and rm_cfg.url:
                client = RemoteManagerClient(i, rm_cfg, request.app)
                await client.start()
                new_clients.append(client)

    # Stop clients for removed entries
    for i in range(len(cfg.remote_managers), len(clients)):
        await clients[i].stop()

    request.app.state.remote_manager_clients = new_clients
