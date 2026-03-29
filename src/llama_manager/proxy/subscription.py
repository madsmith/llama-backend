from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_manager.proxy.server import ProxyServer

_proxy: ProxyServer | None = None


def set_proxy_server(ps: ProxyServer) -> None:
    global _proxy
    _proxy = ps


def proxy_log(text: str, *, request_id: str | None = None) -> None:
    if _proxy is not None:
        _proxy.log(text, request_id=request_id)
