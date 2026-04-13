from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from llama_manager.util.log_buffer import LogRecord as BufferLogRecord


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------

class SlotParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    temperature: float | None = None
    top_p: float | None = None
    min_p: float | None = None
    chat_format: str | None = None
    n_predict: int | None = None


class SlotNextToken(BaseModel):
    n_decoded: int | None = None
    n_remain: int | None = None
    has_next_token: bool | None = None


class SlotInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    id_task: int | None = None
    n_ctx: int
    is_processing: bool
    speculative: bool
    params: SlotParams | None = None
    next_token: list[SlotNextToken] | None = None
    prompt_progress: float | None = None
    prompt_n_processed: int | None = None
    prompt_n_total: int | None = None
    cancellable: bool | None = None


# ---------------------------------------------------------------------------
# Incoming (client → server)
# ---------------------------------------------------------------------------

class ProxyStatusRequest(BaseModel):
    msg: Literal["proxy_status"] = "proxy_status"


class ServerStatusRequest(BaseModel):
    msg: Literal["server_status"] = "server_status"
    suid: str


class SlotStatusRequest(BaseModel):
    msg: Literal["slot_status"] = "slot_status"
    suid: str


class SubscribeSlotStatusRequest(BaseModel):
    msg: Literal["subscribe_slot_status"] = "subscribe_slot_status"
    suid: str


class UnsubscribeSlotStatusRequest(BaseModel):
    msg: Literal["unsubscribe_slot_status"] = "unsubscribe_slot_status"
    subscription_id: int


class SubscribeEventRequest(BaseModel):
    """Generic event subscription. *id* filters which resource to subscribe to."""
    msg: Literal["subscribe_event"] = "subscribe_event"
    type: str
    subtype: str | None = None
    id: str | None = None


class UnsubscribeEventRequest(BaseModel):
    """Cancel a generic event subscription by its server-assigned subscription_id."""
    msg: Literal["unsubscribe_event"] = "unsubscribe_event"
    type: str
    subtype: str | None = None
    subscription_id: int  # subscription_id from SubscribeEventResponse


class GenerateTokenRequest(BaseModel):
    msg: Literal["generate_token"] = "generate_token"


class GetConfigRequest(BaseModel):
    msg: Literal["get_config"] = "get_config"


class PutConfigRequest(BaseModel):
    msg: Literal["put_config"] = "put_config"
    config: dict[str, Any]


class LoadLogRequest(BaseModel):
    msg: Literal["load_log"] = "load_log"
    type: Literal["proxy", "server"]
    suid: str | None = None  # suid when type="server"
    before_id: str | None = None  # fetch records older than the record with this id
    limit: int = 200  # max records to return


class RemotesRequest(BaseModel):
    msg: Literal["remotes"] = "remotes"


class UplinkStatusRequest(BaseModel):
    msg: Literal["uplink_status"] = "uplink_status"


class ServerControlRequest(BaseModel):
    msg: Literal["server_control"] = "server_control"
    operation: Literal["start", "stop", "restart"]
    suid: str


class PropsRequest(BaseModel):
    msg: Literal["props"] = "props"
    suid: str


IncomingMessage = Annotated[
    Union[
        ProxyStatusRequest,
        ServerStatusRequest,
        SlotStatusRequest,
        SubscribeSlotStatusRequest,
        UnsubscribeSlotStatusRequest,
        SubscribeEventRequest,
        UnsubscribeEventRequest,
        GetConfigRequest,
        PutConfigRequest,
        GenerateTokenRequest,
        LoadLogRequest,
        RemotesRequest,
        UplinkStatusRequest,
        ServerControlRequest,
        PropsRequest,
    ],
    Field(discriminator="msg"),
]


# ---------------------------------------------------------------------------
# Outgoing (server → client)
# ---------------------------------------------------------------------------

class ProxyStatusResponse(BaseModel):
    msg: Literal["proxy_status_response"] = "proxy_status_response"
    state: str
    host: str | None
    port: int | None
    uptime: float | None
    pid: int | None


class ServerStatusResponse(BaseModel):
    msg: Literal["server_status_response"] = "server_status_response"
    suid: str
    state: str
    pid: int | None
    host: str | None
    port: int | None
    uptime: float | None


class SlotStatusResponse(BaseModel):
    msg: Literal["slot_status_response"] = "slot_status_response"
    suid: str
    slots: list[SlotInfo]


class SubscribeSlotStatusResponse(BaseModel):
    msg: Literal["subscribe_slot_status_response"] = "subscribe_slot_status_response"
    subscription_id: int
    suid: str
    slots: list[SlotInfo]


class SlotStatusEvent(BaseModel):
    msg: Literal["slot_status_event"] = "slot_status_event"
    subscription_id: int
    suid: str
    slots: list[SlotInfo]


class SubscribeEventResponse(BaseModel):
    """Confirms a generic subscription; *subscription_id* is the server-assigned
    token used in subsequent EventResponse messages and UnsubscribeEventRequest."""
    msg: Literal["subscribe_event_response"] = "subscribe_event_response"
    subscription_id: int


class EventResponse(BaseModel):
    """Generic event pushed to subscribers."""
    msg: Literal["event"] = "event"
    type: str
    subtype: str | None = None
    id: str | None = None
    data: dict[str, Any]


class GenerateTokenResponse(BaseModel):
    msg: Literal["generate_token_response"] = "generate_token_response"
    token: str


class GetConfigResponse(BaseModel):
    msg: Literal["get_config_response"] = "get_config_response"
    config: dict[str, Any]


class PutConfigResponse(BaseModel):
    msg: Literal["put_config_response"] = "put_config_response"
    config: dict[str, Any]


class WireTextLog(BaseModel):
    type: Literal["text"] = "text"
    text: str


class WireProxyRequest(BaseModel):
    type: Literal["request"] = "request"
    method: str
    path: str
    http_ver: str
    size: int | None = None
    server_name: str | None = None


class WireProxyResponse(BaseModel):
    type: Literal["response"] = "response"
    status: int
    phrase: str
    http_ver: str
    streaming: bool
    complete: bool
    elapsed: float | None = None
    size: int | None = None
    server_name: str | None = None


WireLogData = Annotated[
    Union[WireTextLog, WireProxyRequest, WireProxyResponse],
    Field(discriminator="type"),
]


class LogRecord(BaseModel):
    id: str
    line_number: int
    time: float
    request_id: str | None = None
    data: WireLogData

    @staticmethod
    def from_buffer(r: BufferLogRecord) -> LogRecord:
        from llama_manager.util.log_buffer import ProxyRequest, ProxyResponse
        d = r.data
        if isinstance(d, ProxyRequest):
            wire: WireLogData = WireProxyRequest(
                method=d.method, path=d.path, http_ver=d.http_ver,
                size=d.size, server_name=d.server_name,
            )
        elif isinstance(d, ProxyResponse):
            try:
                phrase = HTTPStatus(d.status).phrase
            except ValueError:
                phrase = ""
            wire = WireProxyResponse(
                status=d.status, phrase=phrase, http_ver=d.http_ver,
                streaming=d.streaming, complete=d.complete,
                elapsed=d.elapsed, size=d.size, server_name=d.server_name,
            )
        else:
            wire = WireTextLog(text=d)
        return LogRecord(id=r.id, line_number=r.line_number, time=r.time, request_id=r.request_id, data=wire)


class LoadLogResponse(BaseModel):
    msg: Literal["load_log_response"] = "load_log_response"
    type: str
    suid: str | None = None
    lines: list[LogRecord]
    has_more: bool = False


class RemoteModelInfo(BaseModel):
    suid: str
    name: str | None
    model_id: str
    state: str
    auto_start: bool = False
    has_ttl: bool = False
    allow_proxy: bool = True


class RemoteManagerInfo(BaseModel):
    index: int
    name: str | None
    url: str
    connection_state: str
    models: list[RemoteModelInfo]


class RemotesResponse(BaseModel):
    msg: Literal["remotes_response"] = "remotes_response"
    remotes: list[RemoteManagerInfo]


class UplinkStatusResponse(BaseModel):
    msg: Literal["uplink_status_response"] = "uplink_status_response"
    enabled: bool
    connected_clients: int


class ServerControlResponse(BaseModel):
    msg: Literal["server_control_response"] = "server_control_response"
    operation: str
    suid: str
    success: bool
    error: str | None = None


class PropsResponse(BaseModel):
    msg: Literal["props_response"] = "props_response"
    suid: str
    props: dict[str, Any] | None
