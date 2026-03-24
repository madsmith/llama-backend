from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse

from llama_manager.llama_client import LlamaClient


async def get_props(model: int = Query(default=0)):
    client = LlamaClient(model)
    data = await client.get_props()
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    return data
