from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from ...llama_client import LlamaClient
from ...remote_manager_client import RemoteModelProxy


async def get_health(request: Request, model: int = Query(default=0)):
    pms = getattr(request.app.state, "process_managers", [])
    pm = pms[model] if model < len(pms) else None

    # For remote-manager-proxied models, return cached health
    if isinstance(pm, RemoteModelProxy):
        cached = pm.get_cached_health()
        if cached is None:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return cached

    client = LlamaClient(model)
    data = await client.get_health()
    if data is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return data
