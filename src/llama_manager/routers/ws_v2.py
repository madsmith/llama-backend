from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from llama_manager.llama_client import LlamaClient
from llama_manager.proxy import ProxyServer
from llama_manager.proxy.active_requests import ActiveRequestManager
from llama_manager.remote_manager_client import RemoteModelProxy
from llama_manager.protocol.ws_messages import (
    IncomingMessage,
    ProxyStatusRequest,
    ProxyStatusResponse,
    ServerStatusRequest,
    ServerStatusResponse,
    SlotStatusRequest,
    SlotStatusResponse,
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

                response = await _dispatch(msg, proxy, ws.app.state)
                if response is not None:
                    await ws.send_text(response.model_dump_json())
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass

    return router


async def _dispatch_proxy_status(
    _msg: ProxyStatusRequest, proxy: ProxyServer, _state: object
) -> ProxyStatusResponse:
    return ProxyStatusResponse(**proxy.status())


async def _dispatch_server_status(
    msg: ServerStatusRequest, _proxy: ProxyServer, state: object
) -> ServerStatusResponse | None:
    managers = state.process_managers  # type: ignore[attr-defined]
    if msg.model < 0 or msg.model >= len(managers):
        return None
    return ServerStatusResponse(model=msg.model, **managers[msg.model].get_status())


async def _dispatch_slot_status(
    msg: SlotStatusRequest, _proxy: ProxyServer, state: object
) -> SlotStatusResponse | None:
    managers = state.process_managers  # type: ignore[attr-defined]
    if msg.model < 0 or msg.model >= len(managers):
        return None
    pm = managers[msg.model]

    if isinstance(pm, RemoteModelProxy):
        cfg = pm._client.config
        url = f"http://{cfg.host}:{cfg.port}/api/status/slots?model={pm.remote_model_index}"
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    slots = resp.json()
                    pm.set_slots(slots)
                    return SlotStatusResponse(model=msg.model, slots=slots)
        except Exception:
            pass
        return SlotStatusResponse(model=msg.model, slots=pm.get_cached_slots())

    slots = await LlamaClient(msg.model).get_slots()
    if slots is None:
        return SlotStatusResponse(model=msg.model, slots=[])

    cancellable = set(ActiveRequestManager.list_cancellable(msg.model))
    progress = pm.get_prompt_progress()
    if progress:
        for slot in slots:
            info = progress.get(slot.get("id"))
            if info:
                slot["prompt_progress"] = info["progress"]
                slot["prompt_n_processed"] = info["n_processed"]
                slot["prompt_n_total"] = info["n_total"]
    for slot in slots:
        slot["cancellable"] = slot.get("id") in cancellable

    return SlotStatusResponse(model=msg.model, slots=slots)


_HANDLERS: dict[type, Callable[..., Awaitable[Any]]] = {
    ProxyStatusRequest: _dispatch_proxy_status,
    ServerStatusRequest: _dispatch_server_status,
    SlotStatusRequest: _dispatch_slot_status,
}


async def _dispatch(
    msg: IncomingMessage,
    proxy: ProxyServer,
    state: object,
) -> ProxyStatusResponse | ServerStatusResponse | SlotStatusResponse | None:
    handler = _HANDLERS.get(type(msg))
    if handler is None:
        return None
    return await handler(msg, proxy, state)  # type: ignore[arg-type]
