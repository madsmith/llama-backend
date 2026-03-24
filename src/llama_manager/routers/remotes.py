from __future__ import annotations

from fastapi import APIRouter, Request

from llama_manager.config import load_config
from llama_manager.remote_manager_client import RemoteManagerClient

router = APIRouter(prefix="/api/remotes", tags=["remotes"])


@router.get("")
async def get_remotes(request: Request):
    clients = getattr(request.app.state, "remote_manager_clients", [])
    assert isinstance(clients, list), "Internal error: remote_manager_clients must be a list"

    result = []
    for client in clients:
        assert isinstance(client, RemoteManagerClient), "Internal error: remote_manager_clients must be a list of RemoteManagerClient"
        result.append(
            {
                "index": client.remote_index,
                "name": client.config.name,
                "url": f"{client.config.host}:{client.config.port}",
                "connection_state": client.connection_state,
                "models": [
                    {
                        "remote_model_index": provider.remote_model_index,
                        "local_index": provider.local_index,
                        "name": provider.name,
                        "state": provider.state.value,
                        "server_id": provider.server_id,
                    }
                    for provider in client.models
                ],
            }
        )
    return result


@router.get("/uplink")
async def get_uplink_status(request: Request):
    cfg = load_config()
    return {
        "enabled": cfg.manager_uplink.enabled,
        "connected_clients": getattr(request.app.state, "uplink_client_count", 0),
    }
