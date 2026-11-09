"""Same-origin passthrough from the web UI's Chat page to the proxy.

Requests go through the proxy (rather than straight to llama-server) so chat
gets the same model routing, JIT start, KV cache and request logging as any
external client.
"""
from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from llama_manager.proxy import ProxyServer
from llama_manager.proxy.handler import BACKEND_HEADER

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager


def _error(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _backend_tree(manager: LlamaManager) -> list[dict]:
    """Every backend the proxy can route to, grouped by the manager that owns it.

    The first group is this manager (local llama-servers and remote API
    models); each connected-or-configured remote manager follows. The suid of
    an entry is what the Chat page sends in the x-llama-backend header.
    """
    local: list[dict] = []
    for suid, m in manager.get_local_models().items():
        cfg = manager.get_model_config(suid)
        local.append({
            "suid": suid,
            "name": m.get_name(),
            "model_id": m.get_model_ids()[0],
            "kind": "local",
            "state": m.get_status().get("state"),
            "priority": cfg.priority if cfg else None,
        })
    for suid, m in manager.get_remote_unmanaged().items():
        cfg = manager.get_model_config(suid)
        local.append({
            "suid": suid,
            "name": m.get_name(),
            "model_id": m.get_model_ids()[0],
            "kind": "remote_api",
            "state": "remote",
            "priority": cfg.priority if cfg else None,
        })
    by_model = lambda b: (b["model_id"], -(b["priority"] or 0))  # noqa: E731
    groups = [{
        "id": manager.get_manager_id(),
        "name": socket.gethostname(),
        "local": True,
        "connection_state": "connected",
        "backends": sorted(local, key=by_model),
    }]
    for client in manager.remote_manager_clients:
        cfg = client.get_config()
        groups.append({
            "id": f"remote-{client.remote_index}",
            "name": cfg.name or f"{cfg.host}:{cfg.port}",
            "local": False,
            "connection_state": client.connection_state,
            "backends": sorted(
                (
                    {
                        "suid": m.get_suid(),
                        "name": m.name,
                        "model_id": m.model_id,
                        "kind": "manager",
                        "state": m.state.value,
                        "priority": None,
                    }
                    for m in client.models
                ),
                key=by_model,
            ),
        })
    return groups


def make_router(proxy: ProxyServer, manager: LlamaManager) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.get("/backends")
    async def backends() -> list[dict]:
        return _backend_tree(manager)

    @router.get("/models", response_model=None)
    async def models() -> Response:
        base = proxy.local_base_url()
        if base is None:
            return _error(503, "Proxy is not running")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                upstream = await client.get(f"{base}/v1/models")
        except httpx.HTTPError:
            return _error(502, "Proxy unreachable")
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    @router.post("/completions", response_model=None)
    async def completions(request: Request) -> Response:
        base = proxy.local_base_url()
        if base is None:
            return _error(503, "Proxy is not running")

        body = await request.body()
        try:
            stream = bool(json.loads(body).get("stream"))
        except (json.JSONDecodeError, AttributeError):
            return _error(400, "Invalid JSON body")

        client = httpx.AsyncClient(timeout=None)
        upstream_req = client.build_request(
            "POST",
            f"{base}/v1/chat/completions",
            content=body,
            headers={
                "content-type": "application/json",
                **({BACKEND_HEADER: pin} if (pin := request.headers.get(BACKEND_HEADER)) else {}),
            },
        )
        try:
            upstream = await client.send(upstream_req, stream=stream)
        except httpx.HTTPError:
            await client.aclose()
            return _error(502, "Proxy unreachable")

        if not stream:
            content = upstream.content
            await client.aclose()
            return Response(
                content=content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
            )

        async def _close() -> None:
            await upstream.aclose()
            await client.aclose()

        # Closing the upstream stream when the browser disconnects lets the
        # proxy notice and stop generation.
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            background=BackgroundTask(_close),
        )

    return router
