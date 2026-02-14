from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse

from ... import llama_client


async def get_health(model: int = Query(default=0)):
    data = await llama_client.get_health(model)
    if data is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return data
