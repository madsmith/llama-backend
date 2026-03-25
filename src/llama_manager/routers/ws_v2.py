from __future__ import annotations

import asyncio
import json
from typing import Callable, ClassVar

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from llama_manager.llama_manager import LlamaManager
from llama_manager.process_manager import ProcessManager
from llama_manager.remote_manager_client import RemoteModelProxy
from llama_manager.proxy.active_requests import ActiveRequestManager
from llama_manager.config import AppConfig
from llama_manager.protocol.ws_messages import (
    EventResponse,
    GenerateTokenRequest,
    GenerateTokenResponse,
    GetConfigRequest,
    GetConfigResponse,
    IncomingMessage,
    LoadLogRequest,
    LoadLogResponse,
    LogLine,
    ProxyStatusRequest,
    ProxyStatusResponse,
    PutConfigRequest,
    PutConfigResponse,
    ServerStatusRequest,
    ServerStatusResponse,
    SlotStatusRequest,
    SlotStatusResponse,
    SlotStatusEvent,
    SubscribeEventRequest,
    SubscribeEventResponse,
    SubscribeSlotStatusRequest,
    SubscribeSlotStatusResponse,
    UnsubscribeEventRequest,
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
        SubscribeEventRequest: "_on_subscribe_event",
        UnsubscribeEventRequest: "_on_unsubscribe_event",
        GetConfigRequest: "_on_get_config",
        PutConfigRequest: "_on_put_config",
        GenerateTokenRequest: "_on_generate_token",
        LoadLogRequest: "_on_load_log",
    }

    def __init__(self, manager: LlamaManager, ws: WebSocket) -> None:
        self.manager = manager
        self.ws = ws
        self.outgoing: asyncio.Queue[str] = asyncio.Queue()
        # Maps subscription_id → teardown callable; covers both typed and generic subs.
        self._subscriptions: dict[int, Callable[[], None]] = {}

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
            for teardown in self._subscriptions.values():
                teardown()

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
        for pm in self.manager.get_process_managers():
            server_id = (
                pm.get_server_identifier() if isinstance(pm, ProcessManager)
                else pm.server_id if isinstance(pm, RemoteModelProxy)
                else None
            )
            if server_id == msg.id:
                return ServerStatusResponse(id=msg.id, **pm.get_status())
        return None

    async def _on_slot_status(self, msg: SlotStatusRequest) -> BaseModel | None:
        pms = self.manager.get_process_managers()
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
            self._subscriptions[handle] = lambda h=handle: self.manager.slot_status.unsubscribe(h)

        return SubscribeSlotStatusResponse(
            subscription_id=handle,
            model=msg.model,
            slots=slots,
        )

    async def _on_unsubscribe_slot_status(self, msg: UnsubscribeSlotStatusRequest) -> None:
        teardown = self._subscriptions.pop(msg.subscription_id, None)
        if teardown is not None:
            teardown()

    async def _on_subscribe_event(self, msg: SubscribeEventRequest) -> BaseModel:
        if msg.type == "slots":
            return await self._on_subscribe_event_slots(msg)
        if msg.type == "server_status":
            return await self._on_subscribe_event_server_status(msg)
        if msg.type == "log":
            return await self._on_subscribe_event_log(msg)
        return SubscribeEventResponse(subscription_id=-1)

    async def _on_subscribe_event_slots(self, msg: SubscribeEventRequest) -> BaseModel:
        model = int(msg.id) if msg.id is not None else 0
        pms = self.manager.get_process_managers()
        server_id: str | None = None
        if 0 <= model < len(pms):
            pm = pms[model]
            if isinstance(pm, ProcessManager):
                server_id = pm.get_server_identifier()
            elif isinstance(pm, RemoteModelProxy):
                server_id = pm.server_id

        subscription_id = -1
        if server_id is not None:
            def _on_change(updated_slots: list[dict]) -> None:
                self._push(EventResponse(
                    type="slots",
                    id=str(model),
                    event_data={"model": model, "slots": updated_slots},
                ))

            subscription_id = self.manager.slot_status.subscribe(server_id, _on_change)
            self._subscriptions[subscription_id] = lambda h=subscription_id: self.manager.slot_status.unsubscribe(h)

        return SubscribeEventResponse(subscription_id=subscription_id)

    async def _on_subscribe_event_server_status(self, msg: SubscribeEventRequest) -> BaseModel:
        server_id = msg.id
        q = self.manager.event_bus.subscribe("server_status")

        async def _listen() -> None:
            try:
                while True:
                    event = await q.get()
                    if event.get("id") != server_id:
                        continue
                    self._push(EventResponse(
                        type="server_status",
                        id=msg.id,
                        event_data=event.get("data", {}),
                    ))
            except asyncio.CancelledError:
                pass
            finally:
                self.manager.event_bus.unsubscribe(q)

        task = asyncio.create_task(_listen())
        subscription_id = id(task)
        self._subscriptions[subscription_id] = task.cancel
        return SubscribeEventResponse(subscription_id=subscription_id)

    async def _on_subscribe_event_log(self, msg: SubscribeEventRequest) -> BaseModel:
        if msg.sub_type == "proxy":
            event_type = "proxy_log"

            async def _listen() -> None:
                q = self.manager.event_bus.subscribe(event_type)
                try:
                    while True:
                        event = await q.get()
                        self._push(EventResponse(
                            type="log",
                            sub_type="proxy",
                            event_data=event.get("data", {}),
                        ))
                except asyncio.CancelledError:
                    pass
                finally:
                    self.manager.event_bus.unsubscribe(q)

        elif msg.sub_type == "server":
            server_id = msg.id

            async def _listen() -> None:
                q = self.manager.event_bus.subscribe("server_log")
                try:
                    while True:
                        event = await q.get()
                        if event.get("id") != server_id:
                            continue
                        self._push(EventResponse(
                            type="log",
                            sub_type="server",
                            id=server_id,
                            event_data=event.get("data", {}),
                        ))
                except asyncio.CancelledError:
                    pass
                finally:
                    self.manager.event_bus.unsubscribe(q)

        else:
            return SubscribeEventResponse(subscription_id=-1)

        task = asyncio.create_task(_listen())
        subscription_id = id(task)
        self._subscriptions[subscription_id] = task.cancel
        return SubscribeEventResponse(subscription_id=subscription_id)

    async def _on_load_log(self, msg: LoadLogRequest) -> BaseModel:
        if msg.type == "proxy":
            lines = self.manager.proxy.log_buffer.snapshot()
            return LoadLogResponse(
                type="proxy",
                lines=[LogLine(id=l.id, text=l.text, request_id=l.request_id) for l in lines],
            )
        # type == "server"
        for pm in self.manager.get_process_managers():
            server_id = (
                pm.get_server_identifier() if isinstance(pm, ProcessManager)
                else pm.server_id if isinstance(pm, RemoteModelProxy)
                else None
            )
            if server_id == msg.id and isinstance(pm, ProcessManager):
                lines = pm.log_buffer.snapshot()
                return LoadLogResponse(
                    type="server",
                    id=msg.id,
                    lines=[LogLine(id=l.id, text=l.text, request_id=l.request_id) for l in lines],
                )
        return LoadLogResponse(type="server", id=msg.id, lines=[])

    async def _on_unsubscribe_event(self, msg: UnsubscribeEventRequest) -> None:
        teardown = self._subscriptions.pop(msg.subscription_id, None)
        if teardown is not None:
            teardown()

    async def _on_generate_token(self, _msg: GenerateTokenRequest) -> BaseModel:
        return GenerateTokenResponse(token=self.manager.generate_token())

    async def _on_get_config(self, _msg: GetConfigRequest) -> BaseModel:
        from llama_manager.config import load_config
        return GetConfigResponse(config=load_config().model_dump())

    async def _on_put_config(self, msg: PutConfigRequest) -> BaseModel:
        config = AppConfig.model_validate(msg.config)
        updated = await self.manager.apply_config(config)
        return PutConfigResponse(config=updated.model_dump())


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
