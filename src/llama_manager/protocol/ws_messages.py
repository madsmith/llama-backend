from __future__ import annotations

from typing import Annotated, Literal, Union

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
    model: int = 0


class SlotStatusRequest(BaseModel):
    msg: Literal["slot_status"] = "slot_status"
    model: int = 0


class SubscribeSlotStatusRequest(BaseModel):
    msg: Literal["subscribe_slot_status"] = "subscribe_slot_status"
    model: int = 0


class UnsubscribeSlotStatusRequest(BaseModel):
    msg: Literal["unsubscribe_slot_status"] = "unsubscribe_slot_status"
    subscription_id: int


IncomingMessage = Annotated[
    Union[
        ProxyStatusRequest,
        ServerStatusRequest,
        SlotStatusRequest,
        SubscribeSlotStatusRequest,
        UnsubscribeSlotStatusRequest,
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
    model: int
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
