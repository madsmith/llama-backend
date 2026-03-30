from __future__ import annotations

import httpx


class LlamaClient:
    def __init__(self, base_url: str):
        self._base_url = base_url

    async def _get(self, path: str) -> dict | list | None:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, httpx.ConnectError, ValueError):
            return None

    async def _post(self, path: str, body: dict) -> httpx.Response | None:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                return await client.post(url, json=body)
        except Exception:
            return None

    async def get_health(self) -> dict | None:
        url = f"{self._base_url}/health"
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

    async def slot_restore(self, slot_id: int, filename: str) -> bool:
        """POST /slots/<id>?action=restore. Returns True on success."""
        resp = await self._post(f"/slots/{slot_id}?action=restore", {"filename": filename})
        return resp is not None and resp.status_code == 200

    async def slot_save(self, slot_id: int, filename: str) -> bool:
        """POST /slots/<id>?action=save. Returns True on success."""
        resp = await self._post(f"/slots/{slot_id}?action=save", {"filename": filename})
        return resp is not None and resp.status_code == 200
