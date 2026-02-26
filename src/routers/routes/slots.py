from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from ...llama_client import LlamaClient
from ...proxy.active_requests import cancel, list_cancellable


async def get_slots(request: Request, model: int = Query(default=0)):
    pms = getattr(request.app.state, "process_managers", [])
    pm = pms[model] if model < len(pms) else None
    client = LlamaClient(model)
    data = await client.get_slots()
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    cancellable = set(list_cancellable(model))
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
    if cancel(model, slot):
        return {"status": "cancelled"}
    return JSONResponse(
        {"error": "no active cancellable request on this slot"}, status_code=404
    )
