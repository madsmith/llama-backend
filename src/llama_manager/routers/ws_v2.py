from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from llama_manager.proxy import ProxyServer
from llama_manager.protocol.ws_messages import (
    IncomingMessage,
    ProxyStatusRequest,
    ProxyStatusResponse,
    ServerStatusRequest,
    ServerStatusResponse,
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

                response = _dispatch(msg, proxy, ws.app.state)
                if response is not None:
                    await ws.send_text(response.model_dump_json())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass

    return router


def _dispatch_proxy_status(
    _msg: ProxyStatusRequest, proxy: ProxyServer, _state: object
) -> ProxyStatusResponse:
    return ProxyStatusResponse(**proxy.status())


def _dispatch_server_status(
    msg: ServerStatusRequest, _proxy: ProxyServer, state: object
) -> ServerStatusResponse | None:
    managers = state.process_managers  # type: ignore[attr-defined]
    if msg.model < 0 or msg.model >= len(managers):
        return None
    return ServerStatusResponse(model=msg.model, **managers[msg.model].get_status())


_HANDLERS = {
    ProxyStatusRequest: _dispatch_proxy_status,
    ServerStatusRequest: _dispatch_server_status,
}


def _dispatch(
    msg: IncomingMessage,
    proxy: ProxyServer,
    state: object,
) -> ProxyStatusResponse | ServerStatusResponse | None:
    handler = _HANDLERS.get(type(msg))
    if handler is None:
        return None
    return handler(msg, proxy, state)  # type: ignore[arg-type]