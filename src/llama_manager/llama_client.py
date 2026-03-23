from __future__ import annotations

import httpx

from .config import load_config


class LlamaClient:
    def __init__(self, model_index: int):
        self.model_index = model_index

    def _base_url(self) -> str | None:
        cfg = load_config()
        if self.model_index < 0 or self.model_index >= len(cfg.models):
            return None
        m = cfg.models[self.model_index]
        if m.type == "remote":
            return m.remote_address.rstrip("/") if m.remote_address else None
        return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port + self.model_index}"

    async def _get(self, path: str) -> dict | list | None:
        base = self._base_url()
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

    async def get_health(self) -> dict | None:
        base = self._base_url()
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

    async def get_slots(self) -> list | None:
        result = await self._get("/slots")
        assert result is None or isinstance(result, list)
        return result

    async def get_props(self) -> dict | None:
        result = await self._get("/props")
        assert result is None or isinstance(result, dict)
        return result
