from __future__ import annotations

from .subscription import proxy_log

# ---------------------------------------------------------------------------
# Structured log helpers — format: [time] <arrow> [route] <message>
# ---------------------------------------------------------------------------

_STATUS_TEXT = {
    200: "OK",
    201: "Created",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    return f"{n / 1024:.1f}KB"


def log_req(
    server_name: str | None,
    method: str,
    path: str,
    http_ver: str = "1.1",
    size: int | None = None,
    request_id: str | None = None,
) -> None:
    route = f"[{server_name}]" if server_name else ""
    msg = f"{method} {path} HTTP/{http_ver}"
    if size is not None:
        msg += f" [{_fmt_size(size)}]"
    proxy_log(
        f"\u2192 {route} {msg}" if route else f"\u2192 {msg}",
        request_id=request_id,
    )


def log_resp(
    server_name: str | None,
    status: int,
    http_ver: str = "1.1",
    *,
    streaming: bool = False,
    elapsed: float | None = None,
    size: int | None = None,
    request_id: str | None = None,
) -> None:
    route = f"[{server_name}]" if server_name else ""
    text = _STATUS_TEXT.get(status, "")
    msg = f"HTTP/{http_ver} {status}"
    if text:
        msg += f" {text}"
    if streaming:
        msg += " streaming"
    if elapsed is not None:
        msg += f" ({elapsed:.2f}s)"
    if size is not None:
        msg += f" [{_fmt_size(size)}]"
    proxy_log(
        f"\u2190 {route} {msg}" if route else f"\u2190 {msg}",
        request_id=request_id,
    )


def log_stream_end(
    server_name: str | None,
    elapsed: float,
    size: int,
    request_id: str | None = None,
) -> None:
    route = f"[{server_name}]" if server_name else ""
    msg = f"stream complete ({elapsed:.2f}s) [{_fmt_size(size)}]"
    proxy_log(
        f"\u2190 {route} {msg}" if route else f"\u2190 {msg}",
        request_id=request_id,
    )
