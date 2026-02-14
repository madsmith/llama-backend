from __future__ import annotations

import json
import time

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from .anthropic import handle_anthropic
from .lifecycle import ensure_model_server, touch_model
from .logging import log_req, log_resp, log_stream_end
from .models import (
    BACKEND_ERRORS,
    backend_error_msg,
    default_backend,
    resolve_backend,
    resolve_model_index,
    resolve_server_name,
    rewrite_model_field,
)
from .subscription import proxy_log
from .translate import normalize_messages


async def _stream_passthrough(
    backend: str, path: str, body: dict, t0: float, server_name: str, request: Request
) -> StreamingResponse:
    async def generate():
        total_bytes = 0
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{backend}/v1/{path}",
                    json=body,
                    timeout=None,
                ) as resp:
                    log_resp(
                        server_name,
                        resp.status_code,
                        streaming=True,
                        elapsed=time.monotonic() - t0,
                    )
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            proxy_log(
                                f"\u2190 [{server_name}] client disconnected, aborting stream"
                            )
                            return
                        chunk = line + "\n"
                        total_bytes += len(chunk.encode())
                        yield chunk
            log_stream_end(server_name, time.monotonic() - t0, total_bytes)
        except BACKEND_ERRORS as exc:
            msg = backend_error_msg(exc)
            log_resp(server_name, 502, elapsed=time.monotonic() - t0)
            proxy_log(f"[{server_name}] {msg}")
            error = json.dumps({"error": {"message": msg, "type": "server_error"}})
            yield f"data: {error}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


async def openai_proxy(path: str, request: Request):
    # Intercept Anthropic messages endpoint
    if path == "messages":
        return await handle_anthropic(request)

    method = request.method
    http_ver = request.scope.get("http_version", "1.1")
    req_size = int(request.headers.get("content-length", 0)) or None
    t0 = time.monotonic()
    server_name: str | None = None

    try:
        if method == "POST":
            body = await request.json()
            model_id = body.get("model")

            model_index = resolve_model_index(model_id)
            backend = resolve_backend(model_id)
            if backend is None or model_index is None:
                log_req(None, method, f"/v1/{path}", http_ver, req_size)
                log_resp(None, 404)
                return JSONResponse(
                    {
                        "error": {
                            "message": f"Model not found: {model_id}",
                            "type": "not_found",
                        }
                    },
                    status_code=404,
                )

            body = normalize_messages(body, model_index)
            server_name = resolve_server_name(model_id)
            log_req(server_name, method, f"/v1/{path}", http_ver, req_size)

            try:
                await ensure_model_server(model_index)
            except RuntimeError as exc:
                log_resp(server_name, 503, elapsed=time.monotonic() - t0)
                return JSONResponse(
                    {"error": {"message": str(exc), "type": "server_error"}},
                    status_code=503,
                )

            touch_model(model_index)
            body = rewrite_model_field(body, model_id)

            # Streaming SSE passthrough
            if body.get("stream"):
                return await _stream_passthrough(
                    backend, path, body, t0, server_name, request
                )

            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    f"{backend}/v1/{path}",
                    json=body,
                    timeout=None,
                )
            elapsed = time.monotonic() - t0
            log_resp(
                server_name, resp.status_code, elapsed=elapsed, size=len(resp.content)
            )
            return JSONResponse(resp.json(), status_code=resp.status_code)
        else:
            backend = default_backend()
            server_name = resolve_server_name(None)
            log_req(server_name, method, f"/v1/{path}", http_ver)

            try:
                await ensure_model_server(0)
            except RuntimeError as exc:
                log_resp(server_name, 503, elapsed=time.monotonic() - t0)
                return JSONResponse(
                    {"error": {"message": str(exc), "type": "server_error"}},
                    status_code=503,
                )

            touch_model(0)
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    f"{backend}/v1/{path}",
                    timeout=None,
                )
            elapsed = time.monotonic() - t0
            log_resp(
                server_name, resp.status_code, elapsed=elapsed, size=len(resp.content)
            )
            return JSONResponse(resp.json(), status_code=resp.status_code)

    except BACKEND_ERRORS as exc:
        elapsed = time.monotonic() - t0
        msg = backend_error_msg(exc)
        log_resp(server_name, 502, elapsed=elapsed)
        proxy_log(f"[{server_name}] {msg}")
        return JSONResponse(
            {"error": {"message": msg, "type": "server_error"}},
            status_code=502,
        )
