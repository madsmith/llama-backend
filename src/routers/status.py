from __future__ import annotations

from fastapi import APIRouter, Query
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
async def slots(model: int = Query(default=0)):
    data = await llama_client.get_slots(model)
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data


@router.get("/props")
async def props(model: int = Query(default=0)):
    data = await llama_client.get_props(model)
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data
