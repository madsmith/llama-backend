from __future__ import annotations

import asyncio
import json
from typing import ClassVar

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from llama_manager.llama_manager import LlamaManager
from llama_manager.process_manager import ProcessManager
from llama_manager.proxy.active_requests import ActiveRequestManager
from llama_manager.protocol.ws_messages import (
    IncomingMessage,
    ProxyStatusRequest,
    ProxyStatusResponse,
    ServerStatusRequest,
    ServerStatusResponse,
    SlotStatusRequest,
    SlotStatusResponse,
    SlotStatusEvent,
    SubscribeSlotStatusRequest,
    SubscribeSlotStatusResponse,
    UnsubscribeSlotStatusRequest,
)


class WsV2Connection:
    """Handles a single /v2/ws/manager WebSocket connection."""

    _HANDLERS: ClassVar[dict[type, str]] = {
        ProxyStatusRequest: "_on_proxy_status",
        ServerStatusRequest: "_on_server_status",
        SlotStatusRequest: "_on_slot_status",
        SubscribeSlotStatusRequest: "_on_subscribe_slot_status",
        UnsubscribeSlotStatusRequest: "_on_unsubscribe_slot_status",
    }

    def __init__(self, manager: LlamaManager, ws: WebSocket) -> None:
        self.manager = manager
        self.ws = ws
        self.outgoing: asyncio.Queue[str] = asyncio.Queue()
        self.subscriptions: list[int] = []

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        adapter: TypeAdapter[IncomingMessage] = TypeAdapter(IncomingMessage)
        sender_task = asyncio.create_task(self._sender())
        try:
            while True:
                try:
                    data = await self.ws.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    msg = adapter.validate_python(json.loads(data))
                except (json.JSONDecodeError, ValidationError):
                    continue
                await self._handle(msg)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            sender_task.cancel()
            for handle in self.subscriptions:
                self.manager.slot_status.unsubscribe(handle)

    # ------------------------------------------------------------------
    # Outgoing
    # ------------------------------------------------------------------

    async def _sender(self) -> None:
        while True:
            await self.ws.send_text(await self.outgoing.get())

    def _push(self, model: BaseModel) -> None:
        self.outgoing.put_nowait(model.model_dump_json())

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _handle(self, msg: IncomingMessage) -> None:
        method_name = self._HANDLERS.get(type(msg))
        if method_name is None:
            return
        handler = getattr(self, method_name, None)
        if handler is None:
            raise NotImplementedError(
                f"{type(self).__name__}._HANDLERS maps {type(msg).__name__!r} "
                f"to {method_name!r} but no such method exists"
            )
        response = await handler(msg)
        if response is not None:
            self._push(response)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _on_proxy_status(self, _msg: ProxyStatusRequest) -> BaseModel:
        return ProxyStatusResponse(**self.manager.proxy.status())

    async def _on_server_status(self, msg: ServerStatusRequest) -> BaseModel | None:
        pms = self.manager.process_managers
        if msg.model < 0 or msg.model >= len(pms):
            return None
        return ServerStatusResponse(model=msg.model, **pms[msg.model].get_status())

    async def _on_slot_status(self, msg: SlotStatusRequest) -> BaseModel | None:
        pms = self.manager.process_managers
        if msg.model < 0 or msg.model >= len(pms):
            return None
        pm = pms[msg.model]

        slots = [dict(s) for s in (await self.manager.slot_status.get_slots(msg.model) or [])]

        if isinstance(pm, ProcessManager):
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

    async def _on_subscribe_slot_status(self, msg: SubscribeSlotStatusRequest) -> BaseModel:
        pms = self.manager.get_process_managers()
        server_id: str | None = None
        if 0 <= msg.model < len(pms):
            pm = pms[msg.model]
            if isinstance(pm, ProcessManager):
                server_id = pm.get_server_identifier()
            elif isinstance(pm, RemoteModelProxy):
                server_id = pm.server_id

        slots = await self.manager.slot_status.get_slots(msg.model) or []

        handle = -1
        if server_id is not None:
            _handle_box: list[int] = []

            def _on_change(updated_slots: list[dict]) -> None:
                self._push(SlotStatusEvent(
                    subscription_id=_handle_box[0],
                    model=msg.model,
                    slots=updated_slots,
                ))

            handle = self.manager.slot_status.subscribe(server_id, _on_change)
            _handle_box.append(handle)
            self.subscriptions.append(handle)

        return SubscribeSlotStatusResponse(
            subscription_id=handle,
            model=msg.model,
            slots=slots,
        )

    async def _on_unsubscribe_slot_status(self, msg: UnsubscribeSlotStatusRequest) -> None:
        self.manager.slot_status.unsubscribe(msg.subscription_id)
        try:
            self.subscriptions.remove(msg.subscription_id)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def make_router(manager: LlamaManager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/v2/ws/manager")
    async def _(ws: WebSocket) -> None:
        await ws.accept()
        await WsV2Connection(manager, ws).run()

    return router
