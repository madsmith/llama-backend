from __future__ import annotations

import asyncio
import time

from ..log_buffer import LogBuffer

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


def proxy_log(text: str) -> None:
    stamped = f"[{time.strftime('%H:%M:%S')}] {text}"
    line = proxy_log_buffer.append(stamped)
    msg = {"type": "log", "id": line.id, "text": line.text}
    for q in list(_proxy_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass
