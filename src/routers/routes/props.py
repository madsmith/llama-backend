from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse

from ... import llama_client


async def get_props(model: int = Query(default=0)):
    data = await llama_client.get_props(model)
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data
