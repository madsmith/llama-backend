from __future__ import annotations

from fastapi import APIRouter, Request

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
                "url": client.cfg.url,
                "connection_state": client.connection_state,
                "models": [
                    {
                        "remote_model_index": p.remote_model_index,
                        "local_index": p.local_index,
                        "name": p.name,
                        "state": p.state.value,
                    }
                    for p in client.models
                ],
            }
        )
    return result
