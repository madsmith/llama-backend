from __future__ import annotations

import httpx

from .config import load_config


def _base_url(model_index: int = 0) -> str | None:
    cfg = load_config()
    if model_index < 0 or model_index >= len(cfg.models):
        return None
    m = cfg.models[model_index]
    if m.type == "remote":
        return m.remote_address.rstrip("/") if m.remote_address else None
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port + model_index}"


async def _get(path: str, model_index: int = 0) -> dict | list | None:
    base = _base_url(model_index)
    if base is None:
        return None
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, httpx.ConnectError, ValueError):
        return None


async def get_health(model_index: int = 0) -> dict | None:
    base = _base_url(model_index)
    if base is None:
        return None
    url = f"{base}/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {"status": "unknown"}
            try:
                return resp.json()
            except ValueError:
                return None
    except (httpx.HTTPError, httpx.ConnectError):
        return None


async def get_slots(model_index: int = 0) -> list | None:
    return await _get("/slots", model_index)


async def get_props(model_index: int = 0) -> dict | None:
    return await _get("/props", model_index)
