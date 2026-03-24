from __future__ import annotations

import httpx
from fastapi import Query, Request
from fastapi.responses import JSONResponse

from ...llama_client import LlamaClient
from ...proxy.active_requests import ActiveRequestManager
from ...remote_manager_client import RemoteModelProxy


async def get_slots(request: Request, model: int = Query(default=0)):
    pms = getattr(request.app.state, "process_managers", [])
    pm = pms[model] if model < len(pms) else None

    # For remote-manager-proxied models, fetch live from the remote manager's API
    if isinstance(pm, RemoteModelProxy):
        cfg = pm._client.config
        url = f"http://{cfg.host}:{cfg.port}/api/status/slots?model={pm.remote_model_index}"
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    pm.set_slots(data)  # keep cache in sync
                    return data
        except Exception:
            pass
        return pm.get_cached_slots()

    client = LlamaClient(model)
    data = await client.get_slots()
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    cancellable = set(ActiveRequestManager.list_cancellable(model))
    if pm is not None:
        progress = pm.get_prompt_progress()
        if progress:
            for slot in data:
                info = progress.get(slot.get("id"))
                if info:
                    slot["prompt_progress"] = info["progress"]
                    slot["prompt_n_processed"] = info["n_processed"]
                    slot["prompt_n_total"] = info["n_total"]
    for slot in data:
        slot["cancellable"] = slot.get("id") in cancellable
    return data


async def cancel_slot(model: int = Query(default=0), slot: int = Query(...)):
    if ActiveRequestManager.cancel(model, slot):
        return {"status": "cancelled"}
    return JSONResponse(
        {"error": "no active cancellable request on this slot"}, status_code=404
    )
