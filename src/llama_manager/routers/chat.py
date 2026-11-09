"""Same-origin passthrough from the web UI's Chat page to the proxy.

Requests go through the proxy (rather than straight to llama-server) so chat
gets the same model routing, JIT start, KV cache and request logging as any
external client.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from llama_manager.proxy import ProxyServer


def _error(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def make_router(proxy: ProxyServer) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

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
            headers={"content-type": "application/json"},
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
