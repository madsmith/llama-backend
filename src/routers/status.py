from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import llama_client

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("/health")
async def health(model: int = Query(default=0)):
    data = await llama_client.get_health(model)
    if data is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return data


@router.get("/slots")
async def slots(request: Request, model: int = Query(default=0)):
    pms = getattr(request.app.state, "process_managers", [])
    pm = pms[model] if model < len(pms) else None
    data = await llama_client.get_slots(model)
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    if pm is not None:
        progress = pm.get_prompt_progress()
        if progress:
            for slot in data:
                info = progress.get(slot.get("id"))
                if info:
                    slot["prompt_progress"] = info["progress"]
                    slot["prompt_n_processed"] = info["n_processed"]
                    slot["prompt_n_total"] = info["n_total"]
    return data


@router.get("/props")
async def props(model: int = Query(default=0)):
    data = await llama_client.get_props(model)
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data
