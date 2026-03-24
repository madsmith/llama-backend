from __future__ import annotations

import httpx
from fastapi import Query, Request
from fastapi.responses import JSONResponse

from llama_manager.llama_client import LlamaClient
from llama_manager.remote_manager_client import RemoteModelProxy


async def get_health(request: Request, model: int = Query(default=0)):
    pms = getattr(request.app.state, "process_managers", [])
    pm = pms[model] if model < len(pms) else None

    # For remote-manager-proxied models, fetch live from the remote manager's API
    if isinstance(pm, RemoteModelProxy):
        cfg = pm._client.config
        url = f"http://{cfg.host}:{cfg.port}/api/status/health?model={pm.remote_model_index}"
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(url)
                if resp.status_code in (200, 503):
                    data = resp.json()
                    pm.set_health(data)
                    return data
        except Exception:
            pass
        cached = pm.get_cached_health()
        return cached if cached is not None else {"status": "unknown"}

    client = LlamaClient(model)
    data = await client.get_health()
    if data is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return data
