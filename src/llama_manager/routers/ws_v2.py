from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from llama_manager.proxy import ProxyServer
from llama_manager.ws_messages import (
    IncomingMessage,
    ProxyStatusRequest,
    ProxyStatusResponse,
)


def make_router(proxy: ProxyServer) -> APIRouter:
    router = APIRouter()

    @router.websocket("/v2/ws/manager")
    async def ui_ws(ws: WebSocket):
        await ws.accept()
        adapter: TypeAdapter[IncomingMessage] = TypeAdapter(IncomingMessage)

        try:
            while True:
                try:
                    data = await ws.receive_text()
                except WebSocketDisconnect:
                    break

                try:
                    raw = json.loads(data)
                    msg = adapter.validate_python(raw)
                except (json.JSONDecodeError, ValidationError):
                    continue

                response = _dispatch(msg, proxy)
                if response is not None:
                    await ws.send_text(response.model_dump_json())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass

    return router


def _dispatch(msg: IncomingMessage, proxy: ProxyServer) -> ProxyStatusResponse | None:
    if isinstance(msg, ProxyStatusRequest):
        return ProxyStatusResponse(**proxy.status())
    return None
