from __future__ import annotations

from fastapi import APIRouter, Request

from ..config import load_config

router = APIRouter(prefix="/api/remotes", tags=["remotes"])


@router.get("")
async def get_remotes(request: Request):
    clients = getattr(request.app.state, "remote_manager_clients", [])
    result = []
    for client in clients:
        result.append(
            {
                "index": client.remote_index,
                "name": client.cfg.name,
                "url": f"{client.cfg.host}:{client.cfg.port}",
                "connection_state": client.connection_state,
                "models": [
                    {
                        "remote_model_index": p.remote_model_index,
                        "local_index": p.local_index,
                        "name": p.name,
                        "state": p.state.value,
                        "server_id": p.server_id,
                    }
                    for p in client.models
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
