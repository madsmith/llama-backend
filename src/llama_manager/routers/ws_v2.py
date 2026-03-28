from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from llama_manager.config import load_config
from llama_manager.llama_client import LlamaClient
from llama_manager.llama_manager import LlamaManager
from llama_manager.process_manager import ProcessManager
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
        for pm in self.manager.get_process_managers().values():
            if pm.get_server_identifier() == msg.id:
                return ServerStatusResponse(id=msg.id, **pm.get_status())
        for proxy in self.manager.get_remote_models():
            if proxy.server_id == msg.id:
                return ServerStatusResponse(id=msg.id, **proxy.get_status())
        return None

    @request_handler(SlotStatusRequest)
    async def _on_slot_status(self, msg: SlotStatusRequest) -> BaseModel | None:
        # Remote model proxy: use cached slots or request from remote manager.
        for proxy in self.manager.get_remote_models():
            if proxy.server_id == msg.server_id:
                slots = await proxy.get_slots()
                return SlotStatusResponse(
                    server_id=msg.server_id,
                    slots=[dict(s) for s in slots],
                )

        # Find the local ProcessManager for this server_id.
        pm: ProcessManager | None = None
        model_index: int | None = None
        for i_str, p in self.manager.get_process_managers().items():
            if p.get_server_identifier() == msg.server_id:
                pm = p
                model_index = int(i_str)
                break

        slots = [dict(s) for s in (await self.manager.slot_status.get_slots(msg.server_id) or [])]

        if pm is not None and model_index is not None:
            cancellable = set(ActiveRequestManager.list_cancellable(model_index))
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

        return SlotStatusResponse(server_id=msg.server_id, slots=slots)

    @request_handler(SubscribeSlotStatusRequest)
    async def _on_subscribe_slot_status(self, msg: SubscribeSlotStatusRequest) -> BaseModel:
        server_id = msg.server_id
        slots = await self.manager.slot_status.get_slots(server_id) or []

        _handle_box: list[int] = []

        def _on_change(updated_slots: list[dict]) -> None:
            self._push(SlotStatusEvent(
                subscription_id=_handle_box[0],
                server_id=server_id,
                slots=updated_slots,
            ))

        handle = self.manager.slot_status.subscribe(server_id, _on_change)
        _handle_box.append(handle)
        self._subscriptions[handle] = lambda h=handle: self.manager.slot_status.unsubscribe(h)

        return SubscribeSlotStatusResponse(
            subscription_id=handle,
            server_id=server_id,
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
        server_id = msg.id  # client sends the server_id directly as the event id

        subscription_id = -1
        if server_id is not None:
            def _on_change(updated_slots: list[dict]) -> None:
                self._push(EventResponse(
                    type="slots",
                    id=server_id,
                    event_data={"server_id": server_id, "slots": updated_slots},
                ))

            subscription_id = self.manager.slot_status.subscribe(server_id, _on_change)
            self._subscriptions[subscription_id] = lambda h=subscription_id: self.manager.slot_status.unsubscribe(h)

        return SubscribeEventResponse(subscription_id=subscription_id)

    @event_handler("server_status")
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

    @event_handler("log")
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

    @request_handler(LoadLogRequest)
    async def _on_load_log(self, msg: LoadLogRequest) -> BaseModel:
        if msg.type == "proxy":
            lines = self.manager.proxy.log_buffer.snapshot()
            return LoadLogResponse(
                type="proxy",
                lines=[LogLine(id=l.id, text=l.text, request_id=l.request_id) for l in lines],
            )
        # type == "server"
        for pm in self.manager.get_process_managers().values():
            if pm.get_server_identifier() == msg.id:
                lines = pm.log_buffer.snapshot()
                return LoadLogResponse(
                    type="server",
                    id=msg.id,
                    lines=[LogLine(id=l.id, text=l.text, request_id=l.request_id) for l in lines],
                )
        return LoadLogResponse(type="server", id=msg.id, lines=[])

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
        from llama_manager.config import load_config
        return GetConfigResponse(config=load_config().model_dump())

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
                name=client.config.name,
                url=f"{client.config.host}:{client.config.port}",
                connection_state=client.connection_state,
                models=[
                    RemoteModelInfo(
                        remote_model_index=m.remote_model_index,
                        name=m.name,
                        model_id=m.model_id,
                        state=m.state.value,
                        server_id=m.server_id,
                    )
                    for m in client.models
                ],
            )
            for client in self.manager.remote_manager_clients
        ]
        return RemotesResponse(remotes=remotes)

    @request_handler(ServerControlRequest)
    async def _on_server_control(self, msg: ServerControlRequest) -> BaseModel:
        for i_str, pm in self.manager.get_process_managers().items():
            if pm.get_server_identifier() == msg.server_id and int(i_str) == msg.model_suid:
                try:
                    if msg.operation == "start":
                        asyncio.create_task(pm.start())
                    elif msg.operation == "stop":
                        asyncio.create_task(pm.stop())
                    elif msg.operation == "restart":
                        asyncio.create_task(pm.restart())
                except Exception as exc:
                    return ServerControlResponse(operation=msg.operation, server_id=msg.server_id, success=False, error=str(exc))
                return ServerControlResponse(operation=msg.operation, server_id=msg.server_id, success=True)
        for proxy in self.manager.get_remote_models():
            if proxy.server_id == msg.server_id and proxy.remote_model_index == msg.model_suid:
                await proxy.send_command(msg.operation)
                return ServerControlResponse(operation=msg.operation, server_id=msg.server_id, success=True)
        return ServerControlResponse(operation=msg.operation, server_id=msg.server_id, success=False, error="Server not found")

    @request_handler(UplinkStatusRequest)
    async def _on_uplink_status(self, _msg: UplinkStatusRequest) -> BaseModel:
        return UplinkStatusResponse(
            enabled=self.manager.config.manager_uplink.enabled,
            connected_clients=self.manager.uplink_client_count,
        )


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
        cfg = load_config()
        pms = self.manager.get_process_managers()
        local_pms = [
            (int(i_str), pm)
            for i_str, pm in pms.items()
            if int(i_str) < len(cfg.models)
        ]
        server_id_to_index = {pm.get_server_identifier(): i for i, pm in local_pms}

        # Snapshot
        await self.ws.send_json({
            "type": "snapshot",
            "proxy_port": cfg.api_server.port,
            "manager_id": cfg.manager_id,
            "models": [
                {
                    "index": i,
                    "name": cfg.models[i].name,
                    "model_id": cfg.models[i].effective_id,
                    "process_identifier": pm.process_manager_id,
                    "server_id": pm.get_server_identifier(),
                    "state": pm.get_status()["state"],
                    "llama_port": pm.port,
                }
                for i, pm in local_pms
            ],
        })

        # Log history
        for i, pm in local_pms:
            lines = pm.log_buffer.snapshot()
            if lines:
                await self.ws.send_json({
                    "type": "log_history",
                    "model": i,
                    "lines": [{"id": ln.id, "text": ln.text} for ln in lines],
                })

        self.manager.uplink_client_count += 1

        sender_task = asyncio.create_task(self._sender())
        listener_tasks = [
            asyncio.create_task(self._listen_server_status(server_id_to_index)),
            asyncio.create_task(self._listen_server_log(server_id_to_index)),
            asyncio.create_task(self._listen_health(server_id_to_index)),
        ]
        slot_handles: list[int] = []
        for i, pm in local_pms:
            server_id = pm.get_server_identifier()

            def _on_slots(slots: list[dict], _i: int = i, _sid: str = server_id) -> None:
                self._push_json({"type": "slots", "model": _i, "server_id": _sid, "slots": slots})

            slot_handles.append(self.manager.slot_status.subscribe(server_id, _on_slots))

        try:
            while True:
                try:
                    data = await self.ws.receive_text()
                except WebSocketDisconnect:
                    break
                try:
                    await self._handle_command(json.loads(data), local_pms)
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

    async def _listen_server_status(self, server_id_to_index: dict[str, int]) -> None:
        q = self.manager.event_bus.subscribe("server_status")
        try:
            while True:
                event = await q.get()
                sid = event.get("id")
                if sid in server_id_to_index:
                    self._push_json({
                        "type": "state",
                        "model": server_id_to_index[sid],
                        "state": event.get("data", {}).get("state"),
                    })
        except asyncio.CancelledError:
            pass
        finally:
            self.manager.event_bus.unsubscribe(q)

    async def _listen_server_log(self, server_id_to_index: dict[str, int]) -> None:
        q = self.manager.event_bus.subscribe("server_log")
        try:
            while True:
                event = await q.get()
                sid = event.get("id")
                if sid in server_id_to_index:
                    data = event.get("data", {})
                    self._push_json({
                        "type": "log",
                        "model": server_id_to_index[sid],
                        "id": data.get("line_id"),
                        "text": data.get("text"),
                    })
        except asyncio.CancelledError:
            pass
        finally:
            self.manager.event_bus.unsubscribe(q)

    async def _listen_health(self, server_id_to_index: dict[str, int]) -> None:
        q = self.manager.event_bus.subscribe("health")
        try:
            while True:
                event = await q.get()
                sid = event.get("server_id")
                if sid in server_id_to_index:
                    self._push_json({
                        "type": "health",
                        "model": server_id_to_index[sid],
                        "server_id": sid,
                        "health": event.get("health"),
                    })
        except asyncio.CancelledError:
            pass
        finally:
            self.manager.event_bus.unsubscribe(q)

    async def _handle_command(self, cmd: dict, local_pms: list[tuple[int, ProcessManager]]) -> None:
        t = cmd.get("type")
        model_idx = cmd.get("model", 0)
        pm_map = {i: pm for i, pm in local_pms}
        pm = pm_map.get(model_idx)
        if pm is None:
            return
        if t == "start":
            asyncio.create_task(pm.start())
        elif t == "stop":
            asyncio.create_task(pm.stop())
        elif t == "restart":
            asyncio.create_task(pm.restart())
        elif t == "get_slots":
            server_id = pm.get_server_identifier()
            slots = await self.manager.slot_status.get_slots(server_id) or []
            self._push_json({
                "type": "slots_response",
                "model": model_idx,
                "request_id": cmd.get("request_id", ""),
                "slots": slots,
            })
        elif t == "get_health":
            health = await LlamaClient(model_idx).get_health()
            self._push_json({
                "type": "health_response",
                "model": model_idx,
                "request_id": cmd.get("request_id", ""),
                "health": health,
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
    async def _link(ws: WebSocket, token: str = Query(default="")) -> None:
        config = manager.config
        if not config.manager_uplink.enabled:
            await ws.close(code=4403, reason="Uplink disabled")
            return
        if not config.manager_uplink.token or token != config.manager_uplink.token:
            await ws.close(code=4401, reason="Invalid token")
            return
        await ws.accept()
        await UplinkConnection(manager, ws).run()

    return router
