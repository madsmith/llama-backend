from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Incoming (client → server)
# ---------------------------------------------------------------------------

class ProxyStatusRequest(BaseModel):
    msg: Literal["proxy_status"] = "proxy_status"


class ServerStatusRequest(BaseModel):
    msg: Literal["server_status"] = "server_status"
    model: int = 0


IncomingMessage = Annotated[
    Union[ProxyStatusRequest, ServerStatusRequest],
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
