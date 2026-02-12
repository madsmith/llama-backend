from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import llama_client

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("/health")
async def health():
    data = await llama_client.get_health()
    if data is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return data


@router.get("/slots")
async def slots():
    data = await llama_client.get_slots()
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data


@router.get("/props")
async def props():
    data = await llama_client.get_props()
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data
