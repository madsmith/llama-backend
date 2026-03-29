from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from llama_manager.proxy.server import ProxyServer


class RequestRoutes:
    def __init__(self, proxy: ProxyServer) -> None:
        self._proxy = proxy

    async def list_requests(self):
        return self._proxy.list_requests()

    async def get_request(self, request_id: str):
        entry = self._proxy.get_request(request_id)
        if entry is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return entry.to_dict()
