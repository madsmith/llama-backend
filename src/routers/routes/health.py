from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse

from ...llama_client import LlamaClient


async def get_health(model: int = Query(default=0)):
    client = LlamaClient(model)
    data = await client.get_health()
    if data is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return data
