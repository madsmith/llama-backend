from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.manager.backends import LocalManagedModel
from llama_manager.proxy import ActiveRequestManager
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
    LogRecord,
    PropsRequest,
    PropsResponse,
    ProxyStatusRequest,
    ProxyStatusResponse,
    PutConfigRequest,
    PutConfigResponse,
    RemoteManagerInfo,
    RemoteModelInfo,
    RemotesRequest,
    RemotesResponse,
    ServerControlRequest,
    ServerControlResponse,
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
    UplinkStatusRequest,
    UplinkStatusResponse,
)

logger = logging.getLogger(__name__)

LOG_PAGE_SIZE = 200

_handler_map: dict[type, str] = {}
_event_handler_map: dict[str, str] = {}


def request_handler(msg_type: type) -> Callable:
    def deco(fn: Callable) -> Callable:
        _handler_map[msg_type] = fn.__name__
        return fn
    return deco


def event_handler(event_type: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        _event_handler_map[event_type] = fn.__name__
        return fn
    return deco


class WsV2Connection:
    """Handles a single /v2/ws/manager WebSocket connection."""

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
                    logger.warning(f"Invalid message: {data}")
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
        logger.debug(f"Receiving Message: {type(msg).__name__}")
        method_name = _handler_map.get(type(msg))
        if method_name is None:
            logger.warning(f"No handler for message type: {type(msg).__name__}")
            return

        handler = getattr(self, method_name, None)
        if handler is None:
            raise NotImplementedError(
                f"{type(self).__name__} has no method {method_name!r} "
                f"for {type(msg).__name__!r}"
            )

        try:
            response = await handler(msg)
        except Exception:
            logger.exception("Handler %r raised an exception", method_name)
            return

        if response is not None:
            self._push(response)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    @request_handler(ProxyStatusRequest)
    async def _on_proxy_status(self, _msg: ProxyStatusRequest) -> BaseModel:
        return ProxyStatusResponse(**self.manager.proxy.status())

    @request_handler(ServerStatusRequest)
    async def _on_server_status(self, msg: ServerStatusRequest) -> BaseModel | None:
        local_model = self.manager.get_local_models().get(msg.suid)
        if local_model is not None:
            return ServerStatusResponse(suid=msg.suid, **local_model.get_status())
        for proxy in self.manager.get_remote_models():
            if proxy.get_suid() == msg.suid:
                return ServerStatusResponse(suid=msg.suid, **proxy.get_status())
        unmanaged = self.manager.get_remote_unmanaged().get(msg.suid)
        if unmanaged is not None:
            return ServerStatusResponse(suid=msg.suid, **unmanaged.get_status())
        return None

    @request_handler(SlotStatusRequest)
    async def _on_slot_status(self, msg: SlotStatusRequest) -> BaseModel | None:
        suid = msg.suid

        # Remote model proxy: use cached slots or request from remote manager.
        for proxy in self.manager.get_remote_models():
            if proxy.get_suid() == suid:
                slots = await proxy.get_slots()
                return SlotStatusResponse(suid=suid, slots=[dict(s) for s in (slots or [])])

        slots = [dict(s) for s in (await self.manager.slot_status.get_slots(suid) or [])]

        # Annotate with prompt progress and cancellable info for local models.
        local_model = self.manager.get_local_models().get(suid)
        if local_model is not None:
            cancellable = set(ActiveRequestManager.list_cancellable(suid))
            progress = local_model.get_prompt_progress()
            if progress:
                for slot in slots:
                    info = progress.get(slot.get("id"))
                    if info:
                        slot["prompt_progress"] = info["progress"]
                        slot["prompt_n_processed"] = info["n_processed"]
                        slot["prompt_n_total"] = info["n_total"]
            for slot in slots:
                slot["cancellable"] = slot.get("id") in cancellable

        return SlotStatusResponse(suid=suid, slots=slots)

    @request_handler(SubscribeSlotStatusRequest)
    async def _on_subscribe_slot_status(self, msg: SubscribeSlotStatusRequest) -> BaseModel:
        suid = msg.suid
        slots = await self.manager.slot_status.get_slots(suid) or []

        _handle_box: list[int] = []

        def _on_change(updated_slots: list[dict]) -> None:
            self._push(SlotStatusEvent(
                subscription_id=_handle_box[0],
                suid=suid,
                slots=updated_slots,
            ))

        handle = self.manager.slot_status.subscribe(suid, _on_change)
        _handle_box.append(handle)
        self._subscriptions[handle] = lambda h=handle: self.manager.slot_status.unsubscribe(h)

        return SubscribeSlotStatusResponse(
            subscription_id=handle,
            suid=suid,
            slots=slots,
        )

    @request_handler(UnsubscribeSlotStatusRequest)
    async def _on_unsubscribe_slot_status(self, msg: UnsubscribeSlotStatusRequest) -> None:
        teardown = self._subscriptions.pop(msg.subscription_id, None)
        if teardown is not None:
            teardown()

    @request_handler(SubscribeEventRequest)
    async def _on_subscribe_event(self, msg: SubscribeEventRequest) -> BaseModel:
        method_name = _event_handler_map.get(msg.type)
        if method_name is None:
            return SubscribeEventResponse(subscription_id=-1)

        handler = getattr(self, method_name, None)
        if handler is None:
            return SubscribeEventResponse(subscription_id=-1)

        try:
            return await handler(msg)
        except Exception:
            logger.exception("Handler %r raised an exception", method_name)
            return SubscribeEventResponse(subscription_id=-1)

    @event_handler("slots")
    async def _on_subscribe_event_slots(self, msg: SubscribeEventRequest) -> BaseModel:
        suid = msg.id

        subscription_id = -1
        if suid is not None:
            def _on_change(updated_slots: list[dict], _suid: str = suid) -> None:
                slots = [dict(s) for s in updated_slots]
                if self.manager.get_local_models().get(_suid) is not None:
                    cancellable = set(ActiveRequestManager.list_cancellable(_suid))
                    for slot in slots:
                        slot["cancellable"] = slot.get("id") in cancellable
                self._push(EventResponse(
                    type="slots",
                    id=_suid,
                    data={"suid": _suid, "slots": slots},
                ))

            subscription_id = self.manager.slot_status.subscribe(suid, _on_change)
            self._subscriptions[subscription_id] = lambda h=subscription_id: self.manager.slot_status.unsubscribe(h)

        return SubscribeEventResponse(subscription_id=subscription_id)

    @event_handler("server_status")
    async def _on_subscribe_event_server_status(self, msg: SubscribeEventRequest) -> BaseModel:
        suid = msg.id
        q = self.manager.event_bus.subscribe("server_status")

        async def _listen() -> None:
            try:
                while True:
                    event = await q.get()
                    if event.get("id") != suid:
                        continue
                    self._push(EventResponse(
                        type="server_status",
                        id=suid,
                        data=event.get("data", {}),
                    ))
            except asyncio.CancelledError:
                pass
            finally:
                self.manager.event_bus.unsubscribe(q)

        task = asyncio.create_task(_listen())
        subscription_id = id(task)
        self._subscriptions[subscription_id] = task.cancel
        return SubscribeEventResponse(subscription_id=subscription_id)

    @event_handler("health")
    async def _on_subscribe_event_health(self, msg: SubscribeEventRequest) -> BaseModel:
        suid = msg.id
        q = self.manager.event_bus.subscribe("health")

        async def _listen() -> None:
            try:
                while True:
                    event = await q.get()
                    if event.get("id") != suid:
                        continue
                    self._push(EventResponse(
                        type="health",
                        id=suid,
                        data=event.get("data", {}),
                    ))
            except asyncio.CancelledError:
                pass
            finally:
                self.manager.event_bus.unsubscribe(q)

        task = asyncio.create_task(_listen())
        subscription_id = id(task)
        self._subscriptions[subscription_id] = task.cancel

        # Immediately push current health so the client doesn't have to wait
        # for the next data_publisher poll cycle.
        if suid is not None:
            backend = (
                self.manager.get_local_models().get(suid)
                or self.manager.get_remote_unmanaged().get(suid)
                or next((p for p in self.manager.get_remote_models() if p.get_suid() == suid), None)
            )
            if backend is not None:
                try:
                    health = await backend.get_health()
                    if health:
                        self._push(EventResponse(
                            type="health",
                            id=suid,
                            data={"health": health},
                        ))
                except Exception:
                    pass

        return SubscribeEventResponse(subscription_id=subscription_id)

    @event_handler("log")
    async def _on_subscribe_event_log(self, msg: SubscribeEventRequest) -> BaseModel:
        if msg.subtype == "proxy":
            event_type = "proxy_log"

            async def _listen() -> None:
                q = self.manager.event_bus.subscribe(event_type)
                try:
                    while True:
                        event = await q.get()
                        self._push(EventResponse(
                            type="log",
                            subtype="proxy",
                            data=event.get("data", {}),
                        ))
                except asyncio.CancelledError:
                    pass
                finally:
                    self.manager.event_bus.unsubscribe(q)

        elif msg.subtype == "server":
            suid = msg.id

            async def _listen() -> None:
                q = self.manager.event_bus.subscribe("server_log")
                try:
                    while True:
                        event = await q.get()
                        if event.get("id") != suid:
                            continue
                        self._push(EventResponse(
                            type="log",
                            subtype="server",
                            id=suid,
                            data=event.get("data", {}),
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

    @request_handler(LoadLogRequest)
    async def _on_load_log(self, msg: LoadLogRequest) -> BaseModel:
        def _from_local(log_buffer) -> tuple[list[LogRecord], bool]:
            if msg.before_id is not None:
                recs, has_more = log_buffer.before(msg.before_id, msg.limit)
            else:
                snap = log_buffer.snapshot()
                has_more = len(snap) > msg.limit
                recs = snap[-msg.limit:] if has_more else snap
            return [LogRecord.from_buffer(r) for r in recs], has_more

        if msg.type == "proxy":
            lines, has_more = _from_local(self.manager.proxy.get_log_buffer())
            return LoadLogResponse(type="proxy", lines=lines, has_more=has_more)
        # type == "server"
        suid = msg.suid
        local_model = self.manager.get_local_models().get(suid) if suid else None
        if local_model is not None:
            lines, has_more = _from_local(local_model.get_log_buffer())
            return LoadLogResponse(type="server", suid=suid, lines=lines, has_more=has_more)
        for proxy in self.manager.get_remote_models():
            if proxy.get_suid() == suid:
                result = await proxy.fetch_log(msg.before_id, msg.limit)
                if result is not None:
                    raw_lines, has_more = result
                    lines = [LogRecord.model_validate(d) for d in raw_lines]
                    return LoadLogResponse(type="server", suid=suid, lines=lines, has_more=has_more)
                # Fall back to local proxy buffer if uplink unreachable
                lines, has_more = _from_local(proxy.get_log_buffer())
                return LoadLogResponse(type="server", suid=suid, lines=lines, has_more=has_more)
        return LoadLogResponse(type="server", suid=suid, lines=[], has_more=False)

    @request_handler(UnsubscribeEventRequest)
    async def _on_unsubscribe_event(self, msg: UnsubscribeEventRequest) -> None:
        teardown = self._subscriptions.pop(msg.subscription_id, None)
        if teardown is not None:
            teardown()

    @request_handler(GenerateTokenRequest)
    async def _on_generate_token(self, _msg: GenerateTokenRequest) -> BaseModel:
        return GenerateTokenResponse(token=self.manager.generate_token())

    @request_handler(GetConfigRequest)
    async def _on_get_config(self, _msg: GetConfigRequest) -> BaseModel:
        return GetConfigResponse(config=self.manager.config.model_dump())

    @request_handler(PutConfigRequest)
    async def _on_put_config(self, msg: PutConfigRequest) -> BaseModel:
        config = AppConfig.model_validate(msg.config)
        updated = await self.manager.apply_config(config)
        return PutConfigResponse(config=updated.model_dump())

    @request_handler(RemotesRequest)
    async def _on_remotes(self, _msg: RemotesRequest) -> BaseModel:
        remotes = [
            RemoteManagerInfo(
                index=client.remote_index,
                name=client.get_config().name,
                url=f"{client.get_config().host}:{client.get_config().port}",
                connection_state=client.connection_state,
                models=[
                    RemoteModelInfo(
                        suid=m.get_suid(),
                        name=m.name,
                        model_id=m.model_id,
                        state=m.state.value,
                        auto_start=m.auto_start,
                        has_ttl=m.has_ttl,
                        allow_proxy=m.allow_proxy,
                    )
                    for m in client.models
                ],
            )
            for client in self.manager.remote_manager_clients
        ]
        return RemotesResponse(remotes=remotes)

    @request_handler(ServerControlRequest)
    async def _on_server_control(self, msg: ServerControlRequest) -> BaseModel:
        suid = msg.suid
        local_model = self.manager.get_local_models().get(suid)
        if local_model is not None:
            try:
                if msg.operation == "start":
                    asyncio.create_task(local_model.start())
                elif msg.operation == "stop":
                    asyncio.create_task(local_model.stop())
                elif msg.operation == "restart":
                    asyncio.create_task(local_model.restart())
            except Exception as exc:
                return ServerControlResponse(operation=msg.operation, suid=suid, success=False, error=str(exc))
            return ServerControlResponse(operation=msg.operation, suid=suid, success=True)
        for proxy in self.manager.get_remote_models():
            if proxy.get_suid() == suid:
                await proxy.send_command(msg.operation)
                return ServerControlResponse(operation=msg.operation, suid=suid, success=True)
        return ServerControlResponse(operation=msg.operation, suid=suid, success=False, error="Server not found")

    @request_handler(UplinkStatusRequest)
    async def _on_uplink_status(self, _msg: UplinkStatusRequest) -> BaseModel:
        return UplinkStatusResponse(
            enabled=self.manager.config.manager_uplink.enabled,
            connected_clients=self.manager.uplink_client_count,
        )

    @request_handler(PropsRequest)
    async def _on_props(self, msg: PropsRequest) -> BaseModel:
        client = self.manager.get_client(msg.suid)
        if client is not None:
            return PropsResponse(suid=msg.suid, props=await client.get_props())

        for proxy in self.manager.get_remote_models():
            if proxy.get_suid() == msg.suid:
                return PropsResponse(suid=msg.suid, props=await proxy.get_props())

        return PropsResponse(suid=msg.suid, props=None)


# ---------------------------------------------------------------------------
# Uplink connection (/v2/ws/link)
# ---------------------------------------------------------------------------

class UplinkConnection:
    """Handles a single /v2/ws/link WebSocket connection from a downlink manager."""

    def __init__(self, manager: LlamaManager, ws: WebSocket) -> None:
        self.manager = manager
        self.ws = ws
        self.outgoing: asyncio.Queue[str] = asyncio.Queue()

    def _push_json(self, data: dict) -> None:
        self.outgoing.put_nowait(json.dumps(data))

    async def run(self) -> None:
        config = self.manager.config

        # Auth handshake — wait for authenticate message before sending anything
        try:
            raw = await asyncio.wait_for(self.ws.receive_text(), timeout=10.0)
            auth_msg = json.loads(raw)
        except Exception:
            await self.ws.close(code=4400, reason="Auth timeout or invalid message")
            return

        if auth_msg.get("type") != "authenticate":
            await self.ws.close(code=4400, reason="Expected authenticate message")
            return

        token = auth_msg.get("token", "")
        if not config.manager_uplink.token or token != config.manager_uplink.token:
            await self.ws.send_json({"type": "authenticate_response", "success": False, "reason": "Invalid token"})
            await self.ws.close(code=4401, reason="Invalid token")
            return

        await self.ws.send_json({
            "type": "authenticate_response",
            "success": True,
            "manager_id": config.manager_id,
        })

        local_models_dict = self.manager.get_local_models()
        local_models: list[tuple[str, LocalManagedModel]] = [
            (m.suid, local_models_dict[m.suid])
            for m in config.models
            if m.suid in local_models_dict
        ]
        suid_to_cfg = {m.suid: m for m in config.models}
        suid_to_model: dict[str, LocalManagedModel] = {suid: lm for suid, lm in local_models}
        model_suids: set[str] = set(suid_to_model)

        # Snapshot
        await self.ws.send_json({
            "type": "snapshot",
            "proxy_port": config.api_server.port,
            "models": [
                {
                    "suid": suid,
                    "name": lm.get_name(),
                    "model_id": lm.get_model_ids()[0],
                    **lm.get_status(),
                    "llama_port": lm.port,
                    "auto_start": suid_to_cfg[suid].auto_start,
                    "has_ttl": suid_to_cfg[suid].model_ttl is not None,
                    "allow_proxy": suid_to_cfg[suid].allow_proxy,
                }
                for suid, lm in local_models
            ],
        })

        self.manager.uplink_client_count += 1

        sender_task = asyncio.create_task(self._sender())
        listener_tasks = [
            asyncio.create_task(self._listen_server_status(model_suids)),
            asyncio.create_task(self._listen_server_log(model_suids)),
            asyncio.create_task(self._listen_health(model_suids)),
        ]
        slot_handles: list[int] = []
        for suid in model_suids:
            def _on_slots(slots: list[dict], _suid: str = suid) -> None:
                self._push_json({"type": "slots", "suid": _suid, "slots": slots})

            slot_handles.append(self.manager.slot_status.subscribe(suid, _on_slots))

        try:
            while True:
                try:
                    data = await self.ws.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    await self._handle_command(json.loads(data), suid_to_model)
                except Exception:
                    pass
        finally:
            sender_task.cancel()
            for task in listener_tasks:
                task.cancel()
            for handle in slot_handles:
                self.manager.slot_status.unsubscribe(handle)
            self.manager.uplink_client_count -= 1

    async def _sender(self) -> None:
        try:
            while True:
                await self.ws.send_text(await self.outgoing.get())
        except asyncio.CancelledError:
            pass

    async def _listen_server_status(self, model_suids: set[str]) -> None:
        q = self.manager.event_bus.subscribe("server_status")
        try:
            while True:
                event = await q.get()
                suid = event.get("id")
                if suid in model_suids:
                    self._push_json({"type": "state", "suid": suid, **event.get("data", {})})
        except asyncio.CancelledError:
            pass
        finally:
            self.manager.event_bus.unsubscribe(q)

    async def _listen_server_log(self, model_suids: set[str]) -> None:
        q = self.manager.event_bus.subscribe("server_log")
        try:
            while True:
                event = await q.get()
                suid = event.get("id")
                if suid in model_suids:
                    data = event.get("data", {})
                    # data is a WireLogRecord dump: {id, line_number, time, request_id, data: {type, text, ...}}
                    log_data = data.get("data", {})
                    text = log_data.get("text", "") if log_data.get("type") == "text" else str(log_data)
                    self._push_json({
                        "type": "log",
                        "suid": suid,
                        "id": data.get("id"),
                        "text": text,
                    })
        except asyncio.CancelledError:
            pass
        finally:
            self.manager.event_bus.unsubscribe(q)

    async def _listen_health(self, model_suids: set[str]) -> None:
        q = self.manager.event_bus.subscribe("health")
        try:
            while True:
                event = await q.get()
                suid = event.get("id")
                if suid in model_suids:
                    self._push_json({
                        "type": "health",
                        "suid": suid,
                        "health": event.get("data", {}).get("health"),
                    })
        except asyncio.CancelledError:
            pass
        finally:
            self.manager.event_bus.unsubscribe(q)

    async def _handle_command(self, cmd: dict, suid_to_model: dict[str, LocalManagedModel]) -> None:
        t = cmd.get("type")
        suid = cmd.get("suid", "")
        local_model = suid_to_model.get(suid)
        if local_model is None:
            return
        if t == "start":
            asyncio.create_task(local_model.start())
        elif t == "stop":
            asyncio.create_task(local_model.stop())
        elif t == "restart":
            asyncio.create_task(local_model.restart())
        elif t == "get_slots":
            slots = await self.manager.slot_status.get_slots(suid) or []
            self._push_json({
                "type": "slots_response",
                "suid": suid,
                "request_id": cmd.get("request_id", ""),
                "slots": slots,
            })
        elif t == "get_health":
            client = self.manager.get_client_at(local_model.get_base_url())
            health = await client.get_health()
            self._push_json({
                "type": "health_response",
                "suid": suid,
                "request_id": cmd.get("request_id", ""),
                "health": health,
            })
        elif t == "get_props":
            client = self.manager.get_client_at(local_model.get_base_url())
            props = await client.get_props()
            self._push_json({
                "type": "props_response",
                "suid": suid,
                "request_id": cmd.get("request_id", ""),
                "props": props,
            })
        elif t == "get_log":
            before_id: str | None = cmd.get("before_id")
            limit = int(cmd.get("limit") or LOG_PAGE_SIZE)
            if before_id is not None:
                recs, has_more = local_model.log_buffer.before(before_id, limit)
            else:
                snap = local_model.log_buffer.snapshot()
                has_more = len(snap) > limit
                recs = snap[-limit:] if has_more else snap
            self._push_json({
                "type": "log_response",
                "request_id": cmd.get("request_id", ""),
                "suid": suid,
                "has_more": has_more,
                "lines": [
                    {
                        "id": r.id,
                        "line_number": r.line_number,
                        "time": r.time,
                        "request_id": r.request_id,
                        "data": LogRecord.from_buffer(r).data.model_dump(),
                    }
                    for r in recs
                ],
            })


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def make_router(manager: LlamaManager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/v2/ws/manager")
    async def _(ws: WebSocket) -> None:
        await ws.accept()
        await WsV2Connection(manager, ws).run()

    @router.websocket("/v2/ws/link")
    async def _link(ws: WebSocket) -> None:
        config = manager.config
        if not config.manager_uplink.enabled:
            await ws.close(code=4403, reason="Uplink disabled")
            return
        await ws.accept()
        await UplinkConnection(manager, ws).run()

    return router
