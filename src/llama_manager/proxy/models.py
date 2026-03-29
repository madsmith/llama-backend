from __future__ import annotations

import httpx

# ---------------------------------------------------------------------------
# Backend error helpers
# ---------------------------------------------------------------------------

# Transport-level errors when the backend dies or is unreachable
BACKEND_ERRORS = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)


def backend_error_msg(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "Backend server is not reachable"
    return "Backend server disconnected"
