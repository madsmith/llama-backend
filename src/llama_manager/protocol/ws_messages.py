from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


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
    id: str


class SlotStatusRequest(BaseModel):
    msg: Literal["slot_status"] = "slot_status"
    model: int = 0


class SubscribeSlotStatusRequest(BaseModel):
    msg: Literal["subscribe_slot_status"] = "subscribe_slot_status"
    model: int = 0


class UnsubscribeSlotStatusRequest(BaseModel):
    msg: Literal["unsubscribe_slot_status"] = "unsubscribe_slot_status"
    subscription_id: int


class SubscribeEventRequest(BaseModel):
    """Generic event subscription. *id* is a client-chosen correlation token
    returned in the response so callers can match async request/response pairs."""
    msg: Literal["subscribe_event"] = "subscribe_event"
    type: str
    sub_type: str | None = None
    id: str | None = None


class UnsubscribeEventRequest(BaseModel):
    """Cancel a generic event subscription by its server-assigned subscription_id."""
    msg: Literal["unsubscribe_event"] = "unsubscribe_event"
    type: str
    sub_type: str | None = None
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
    id: str | None = None  # server_id when type="server"


class RemotesRequest(BaseModel):
    msg: Literal["remotes"] = "remotes"


class UplinkStatusRequest(BaseModel):
    msg: Literal["uplink_status"] = "uplink_status"


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
    id: str
    state: str
    pid: int | None
    host: str | None
    port: int | None
    uptime: float | None


class SlotStatusResponse(BaseModel):
    msg: Literal["slot_status_response"] = "slot_status_response"
    model: int
    slots: list[SlotInfo]


class SubscribeSlotStatusResponse(BaseModel):
    msg: Literal["subscribe_slot_status_response"] = "subscribe_slot_status_response"
    subscription_id: int
    model: int
    slots: list[SlotInfo]


class SlotStatusEvent(BaseModel):
    msg: Literal["slot_status_event"] = "slot_status_event"
    subscription_id: int
    model: int
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
    sub_type: str | None = None
    id: str | None = None
    event_data: dict[str, Any]


class GenerateTokenResponse(BaseModel):
    msg: Literal["generate_token_response"] = "generate_token_response"
    token: str


class GetConfigResponse(BaseModel):
    msg: Literal["get_config_response"] = "get_config_response"
    config: dict[str, Any]


class PutConfigResponse(BaseModel):
    msg: Literal["put_config_response"] = "put_config_response"
    config: dict[str, Any]


class LogLine(BaseModel):
    id: int
    text: str
    request_id: str | None = None


class LoadLogResponse(BaseModel):
    msg: Literal["load_log_response"] = "load_log_response"
    type: str
    id: str | None = None
    lines: list[LogLine]


class RemoteModelInfo(BaseModel):
    remote_model_index: int
    name: str | None
    state: str
    server_id: str


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
