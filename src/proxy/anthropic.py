from __future__ import annotations

import json
import time
import uuid

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from .translate import FINISH_MAP, anthropic_to_openai, openai_to_anthropic, sse
from .utils import (
    BACKEND_ERRORS,
    _proxy_log,
    backend_error_msg,
    ensure_model_server,
    log_req,
    log_resp,
    log_stream_end,
    resolve_backend,
    resolve_model_index,
    resolve_server_name,
    rewrite_model_field,
    touch_model,
)


async def _handle_anthropic_stream(
    body: dict,
    backend: str,
    model: str,
    t0: float,
    server_name: str,
    request: Request,
) -> StreamingResponse:
    """Translate an OpenAI SSE stream into Anthropic SSE events."""
    oai_body = anthropic_to_openai(body)

    async def generate():
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        input_tokens = 0
        output_tokens = 0
        block_started = False
        finish_reason = "end_turn"
        total_bytes = 0

        def _emit(s: str):
            nonlocal total_bytes
            total_bytes += len(s.encode())
            return s

        # message_start
        yield _emit(
            sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        )

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{backend}/v1/chat/completions",
                    json=oai_body,
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
                            _proxy_log(
                                f"\u2190 [{server_name}] client disconnected, aborting stream"
                            )
                            return
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        # usage chunk (stream_options)
                        if chunk.get("usage"):
                            input_tokens = chunk["usage"].get("prompt_tokens", 0)
                            output_tokens = chunk["usage"].get("completion_tokens", 0)

                        delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                        fr = (chunk.get("choices") or [{}])[0].get("finish_reason")
                        if fr:
                            finish_reason = FINISH_MAP.get(fr, "end_turn")

                        text = delta.get("content")
                        if text is not None:
                            if not block_started:
                                yield _emit(
                                    sse(
                                        "content_block_start",
                                        {
                                            "type": "content_block_start",
                                            "index": 0,
                                            "content_block": {
                                                "type": "text",
                                                "text": "",
                                            },
                                        },
                                    )
                                )
                                block_started = True
                            yield _emit(
                                sse(
                                    "content_block_delta",
                                    {
                                        "type": "content_block_delta",
                                        "index": 0,
                                        "delta": {"type": "text_delta", "text": text},
                                    },
                                )
                            )
        except BACKEND_ERRORS as exc:
            msg = backend_error_msg(exc)
            log_resp(server_name, 502, elapsed=time.monotonic() - t0)
            _proxy_log(f"[{server_name}] {msg}")
            yield _emit(
                sse(
                    "error",
                    {
                        "type": "error",
                        "error": {"type": "server_error", "message": msg},
                    },
                )
            )
            return

        if block_started:
            yield _emit(
                sse(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": 0},
                )
            )

        yield _emit(
            sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": finish_reason, "stop_sequence": None},
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                },
            )
        )
        yield _emit(sse("message_stop", {"type": "message_stop"}))

        log_stream_end(server_name, time.monotonic() - t0, total_bytes)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def handle_anthropic(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    model = body.get("model", "unknown")
    model_id = body.get("model")
    http_ver = request.scope.get("http_version", "1.1")
    req_size = int(request.headers.get("content-length", 0)) or None
    model_index = resolve_model_index(model_id)
    backend = resolve_backend(model_id)
    if backend is None or model_index is None:
        log_req(None, "POST", "/v1/messages", http_ver, req_size)
        log_resp(None, 404)
        return JSONResponse(
            {
                "type": "error",
                "error": {"type": "not_found", "message": f"Model not found: {model}"},
            },
            status_code=404,
        )

    server_name = resolve_server_name(model_id)
    log_req(server_name, "POST", "/v1/messages", http_ver, req_size)
    t0 = time.monotonic()

    try:
        await ensure_model_server(model_index)
    except RuntimeError as exc:
        log_resp(server_name, 503, elapsed=time.monotonic() - t0)
        return JSONResponse(
            {"type": "error", "error": {"type": "server_error", "message": str(exc)}},
            status_code=503,
        )

    touch_model(model_index)
    body = rewrite_model_field(body, model_id)

    if body.get("stream"):
        return await _handle_anthropic_stream(
            body, backend, model, t0, server_name, request
        )

    oai_body = anthropic_to_openai(body)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{backend}/v1/chat/completions",
                json=oai_body,
                timeout=None,
            )
        elapsed = time.monotonic() - t0
        oai_resp = resp.json()
        log_resp(server_name, 200, elapsed=elapsed, size=len(resp.content))
        return JSONResponse(openai_to_anthropic(oai_resp, model))
    except BACKEND_ERRORS as exc:
        elapsed = time.monotonic() - t0
        msg = backend_error_msg(exc)
        log_resp(server_name, 502, elapsed=elapsed)
        _proxy_log(f"[{server_name}] {msg}")
        return JSONResponse(
            {
                "type": "error",
                "error": {"type": "server_error", "message": msg},
            },
            status_code=502,
        )
