from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Incoming (client → server)
# ---------------------------------------------------------------------------

class ProxyStatusRequest(BaseModel):
    msg: Literal["proxy_status"] = "proxy_status"


IncomingMessage = Annotated[
    Union[ProxyStatusRequest],
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
