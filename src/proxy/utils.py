from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..config import load_config
from ..log_buffer import LogBuffer
from ..process_manager import ProcessManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Proxy-scoped log buffer + pub/sub (mirrors ProcessManager pattern)
# ---------------------------------------------------------------------------

proxy_log_buffer = LogBuffer(maxlen=10_000)
_proxy_subscribers: list[asyncio.Queue[dict]] = []


def proxy_subscribe() -> asyncio.Queue[dict]:
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    _proxy_subscribers.append(q)
    return q


def proxy_unsubscribe(q: asyncio.Queue[dict]) -> None:
    try:
        _proxy_subscribers.remove(q)
    except ValueError:
        pass


def shutdown_proxy_subscribers() -> None:
    """Send empty dict sentinel to all subscriber queues so WS handlers exit."""
    for q in list(_proxy_subscribers):
        try:
            q.put_nowait({})
        except asyncio.QueueFull:
            pass


def _proxy_log(text: str) -> None:
    stamped = f"[{time.strftime('%H:%M:%S')}] {text}"
    line = proxy_log_buffer.append(stamped)
    msg = {"type": "log", "id": line.id, "text": line.text}
    for q in list(_proxy_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# Backend error helpers
# ---------------------------------------------------------------------------

# Transport-level errors when the backend dies or is unreachable
BACKEND_ERRORS = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)


def backend_error_msg(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "Backend server is not reachable"
    return "Backend server disconnected"


# ---------------------------------------------------------------------------
# JIT model server start + TTL tracking
# ---------------------------------------------------------------------------

_process_managers: list[ProcessManager | None] = []
_model_last_activity: dict[int, float] = {}
_ttl_task: asyncio.Task | None = None


def touch_model(model_index: int) -> None:
    """Record activity for a model, resetting its TTL timer."""
    _model_last_activity[model_index] = time.monotonic()


async def _ttl_checker() -> None:
    """Background task that stops idle models whose TTL has expired."""
    while True:
        await asyncio.sleep(30)
        try:
            cfg = load_config()
            now = time.monotonic()
            for i, m in enumerate(cfg.models):
                if m.model_ttl is None or m.type == "remote":
                    continue
                if i >= len(_process_managers):
                    continue
                pm = _process_managers[i]
                if pm is None or pm.state.value != "running":
                    continue
                last = _model_last_activity.get(i)
                if last is None:
                    continue
                if now - last > m.model_ttl * 60:
                    name = m.name or f"model-{i}"
                    _proxy_log(f"TTL expired for [{name}], stopping server")
                    await pm.stop()
        except Exception:
            pass  # don't crash the background task


def set_process_managers(pms: list[ProcessManager | None]) -> None:
    global _process_managers
    _process_managers = pms


def get_ttl_task() -> asyncio.Task | None:
    return _ttl_task


def set_ttl_task(task: asyncio.Task | None) -> None:
    global _ttl_task
    _ttl_task = task


async def ensure_model_server(model_index: int = 0) -> None:
    """Start model server on-demand if JIT or TTL is enabled and server isn't running."""
    cfg = load_config()
    m = cfg.models[model_index] if model_index < len(cfg.models) else None
    has_ttl = m is not None and m.model_ttl is not None
    if not cfg.api_server.jit_model_server and not has_ttl:
        return
    if model_index < 0 or model_index >= len(_process_managers):
        return
    pm = _process_managers[model_index]
    if pm is None:
        return  # remote model — no local process to start
    if pm.state.value == "running":
        return
    if pm.state.value not in ("stopped", "error"):
        return

    timeout = cfg.api_server.jit_timeout or 80
    _proxy_log(f"JIT: model server [{model_index}] is {pm.state.value}, starting...")
    await pm.start()

    elapsed = 0.0
    while elapsed < timeout:
        state = pm.state.value
        if state == "running":
            _proxy_log(f"JIT: model server [{model_index}] ready ({elapsed:.1f}s)")
            return
        if state == "error":
            raise RuntimeError(f"Model server [{model_index}] failed to start")
        await asyncio.sleep(0.5)
        elapsed += 0.5

    raise RuntimeError(
        f"Model server [{model_index}] did not become ready within {timeout}s"
    )


# ---------------------------------------------------------------------------
# Model resolution helpers
# ---------------------------------------------------------------------------


def resolve_model_index(model_id: str | None) -> int | None:
    """Resolve a model ID to a model index. Returns None if not found."""
    if not model_id:
        return 0
    cfg = load_config()
    for i, m in enumerate(cfg.models):
        if m.effective_id == model_id:
            return i
    return None


def resolve_backend(model_id: str | None) -> str | None:
    """Resolve a model ID to a backend URL. Returns None if not found."""
    idx = resolve_model_index(model_id)
    if idx is None:
        return None
    cfg = load_config()
    m = cfg.models[idx]
    if m.type == "remote":
        return m.remote_address.rstrip("/") if m.remote_address else None
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port + idx}"


def default_backend() -> str:
    cfg = load_config()
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port}"


def rewrite_model_field(body: dict, model_id: str | None) -> dict:
    """If the target model is remote with a remote_model_id, rewrite the model field."""
    idx = resolve_model_index(model_id)
    if idx is None:
        return body
    cfg = load_config()
    m = cfg.models[idx]
    if m.type == "remote" and m.remote_model_id:
        return {**body, "model": m.remote_model_id}
    return body


def resolve_server_name(model_id: str | None) -> str:
    """Map a model ID to a human-readable server name from config."""
    cfg = load_config()
    if not model_id:
        m = cfg.models[0] if cfg.models else None
        return m.name or m.effective_id if m else "default"
    for m in cfg.models:
        if m.effective_id == model_id:
            return m.name or m.effective_id
    return model_id


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
) -> None:
    route = f"[{server_name}]" if server_name else ""
    msg = f"{method} {path} HTTP/{http_ver}"
    if size is not None:
        msg += f" [{_fmt_size(size)}]"
    _proxy_log(f"\u2192 {route} {msg}" if route else f"\u2192 {msg}")


def log_resp(
    server_name: str | None,
    status: int,
    http_ver: str = "1.1",
    *,
    streaming: bool = False,
    elapsed: float | None = None,
    size: int | None = None,
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
    _proxy_log(f"\u2190 {route} {msg}" if route else f"\u2190 {msg}")


def log_stream_end(
    server_name: str | None,
    elapsed: float,
    size: int,
) -> None:
    route = f"[{server_name}]" if server_name else ""
    msg = f"stream complete ({elapsed:.2f}s) [{_fmt_size(size)}]"
    _proxy_log(f"\u2190 {route} {msg}" if route else f"\u2190 {msg}")
