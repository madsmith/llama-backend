from __future__ import annotations

import httpx

from .config import load_config


async def _get(path: str) -> dict | list | None:
    cfg = load_config()
    url = f"http://{cfg.host}:{cfg.port}{path}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, httpx.ConnectError, ValueError):
        return None


async def get_health() -> dict | None:
    return await _get("/health")


async def get_slots() -> list | None:
    return await _get("/slots")


async def get_props() -> dict | None:
    return await _get("/props")
