from __future__ import annotations

import httpx

from .config import load_config


async def _get(path: str, model_index: int = 0) -> dict | list | None:
    cfg = load_config()
    port = cfg.api_server.llama_server_starting_port + model_index
    url = f"http://127.0.0.1:{port}{path}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, httpx.ConnectError, ValueError):
        return None


async def get_health(model_index: int = 0) -> dict | None:
    return await _get("/health", model_index)


async def get_slots(model_index: int = 0) -> list | None:
    return await _get("/slots", model_index)


async def get_props(model_index: int = 0) -> dict | None:
    return await _get("/props", model_index)
