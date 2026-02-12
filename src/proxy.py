from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, StreamingResponse

from .config import load_config

logger = logging.getLogger(__name__)

proxy_app = FastAPI(title="Llama Proxy")

proxy_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_server: uvicorn.Server | None = None
_task: asyncio.Task | None = None
_proxy_host: str | None = None
_proxy_port: int | None = None
_proxy_started_at: float | None = None


def _resolve_backend(model_id: str | None) -> str | None:
    """Resolve a model ID to a backend URL. Returns None if not found."""
    cfg = load_config()
    starting_port = cfg.api_server.llama_server_starting_port

    if not model_id:
        return f"http://127.0.0.1:{starting_port}"

    for i, m in enumerate(cfg.models):
        if m.effective_id == model_id:
            return f"http://127.0.0.1:{starting_port + i}"

    return None


def _default_backend() -> str:
    cfg = load_config()
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port}"


# ---------------------------------------------------------------------------
# Anthropic /v1/messages translation
# ---------------------------------------------------------------------------

def _anthropic_to_openai(body: dict) -> dict:
    """Convert an Anthropic Messages API request to OpenAI chat/completions."""
    messages: list[dict] = []
    if system := body.get("system"):
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            text = "\n\n".join(
                b.get("text", "") for b in system if b.get("type") == "text"
            )
            if text:
                messages.append({"role": "system", "content": text})
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = "\n".join(parts) if parts else ""
        messages.append({"role": msg["role"], "content": content})

    oai: dict = {
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
    }
    if body.get("model"):
        oai["model"] = body["model"]
    if body.get("temperature") is not None:
        oai["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        oai["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]
    if body.get("stream"):
        oai["stream"] = True
        oai["stream_options"] = {"include_usage": True}
    return oai


_FINISH_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "end_turn",
}


def _openai_to_anthropic(oai_resp: dict, model: str) -> dict:
    """Convert an OpenAI chat/completions response to Anthropic Messages format."""
    choice = oai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    text = message.get("content", "") or ""
    finish = choice.get("finish_reason", "stop") or "stop"
    usage_in = oai_resp.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": _FINISH_MAP.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage_in.get("prompt_tokens", 0),
            "output_tokens": usage_in.get("completion_tokens", 0),
        },
    }


async def _handle_anthropic_stream(
    body: dict, backend: str, model: str
) -> StreamingResponse:
    """Translate an OpenAI SSE stream into Anthropic SSE events."""
    oai_body = _anthropic_to_openai(body)

    async def generate():
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        input_tokens = 0
        output_tokens = 0
        block_started = False
        finish_reason = "end_turn"

        # message_start
        yield _sse(
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

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{backend}/v1/chat/completions",
                    json=oai_body,
                    timeout=None,
                ) as resp:
                    async for line in resp.aiter_lines():
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
                            finish_reason = _FINISH_MAP.get(fr, "end_turn")

                        text = delta.get("content")
                        if text is not None:
                            if not block_started:
                                yield _sse(
                                    "content_block_start",
                                    {
                                        "type": "content_block_start",
                                        "index": 0,
                                        "content_block": {"type": "text", "text": ""},
                                    },
                                )
                                block_started = True
                            yield _sse(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": text},
                                },
                            )
        except httpx.ConnectError:
            yield _sse(
                "error",
                {"type": "error", "error": {"type": "server_error", "message": "Backend unavailable"}},
            )
            return

        if block_started:
            yield _sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            )

        yield _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": finish_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )
        yield _sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _handle_anthropic(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    model = body.get("model", "unknown")
    backend = _resolve_backend(body.get("model"))
    if backend is None:
        return JSONResponse(
            {"type": "error", "error": {"type": "not_found", "message": f"Model not found: {model}"}},
            status_code=404,
        )

    if body.get("stream"):
        return await _handle_anthropic_stream(body, backend, model)

    oai_body = _anthropic_to_openai(body)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{backend}/v1/chat/completions",
                json=oai_body,
                timeout=None,
            )
        oai_resp = resp.json()
        return JSONResponse(_openai_to_anthropic(oai_resp, model))
    except httpx.ConnectError:
        return JSONResponse(
            {
                "type": "error",
                "error": {
                    "type": "server_error",
                    "message": "Backend llama-server is not running",
                },
            },
            status_code=502,
        )


# ---------------------------------------------------------------------------
# OpenAI passthrough
# ---------------------------------------------------------------------------

@proxy_app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openai_proxy(path: str, request: Request):
    # Intercept Anthropic messages endpoint
    if path == "messages":
        return await _handle_anthropic(request)

    method = request.method

    try:
        if method == "POST":
            body = await request.json()

            backend = _resolve_backend(body.get("model"))
            if backend is None:
                return JSONResponse(
                    {"error": {"message": f"Model not found: {body.get('model')}", "type": "not_found"}},
                    status_code=404,
                )

            # Streaming SSE passthrough
            if body.get("stream"):
                return await _stream_passthrough(backend, path, body)

            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    f"{backend}/v1/{path}",
                    json=body,
                    timeout=None,
                )
            return JSONResponse(resp.json(), status_code=resp.status_code)
        else:
            backend = _default_backend()
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    f"{backend}/v1/{path}",
                    timeout=None,
                )
            return JSONResponse(resp.json(), status_code=resp.status_code)

    except httpx.ConnectError:
        return JSONResponse(
            {"error": {"message": "Backend llama-server is not running", "type": "server_error"}},
            status_code=502,
        )


async def _stream_passthrough(backend: str, path: str, body: dict) -> StreamingResponse:
    async def generate():
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{backend}/v1/{path}",
                    json=body,
                    timeout=None,
                ) as resp:
                    async for line in resp.aiter_lines():
                        yield line + "\n"
        except httpx.ConnectError:
            error = json.dumps({"error": {"message": "Backend llama-server is not running", "type": "server_error"}})
            yield f"data: {error}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def start_proxy() -> None:
    global _server, _task, _proxy_host, _proxy_port, _proxy_started_at
    cfg = load_config()
    api = cfg.api_server
    config = uvicorn.Config(
        proxy_app,
        host=api.host,
        port=api.port,
        log_level="debug" if os.environ.get("LLAMA_DEBUG") else "info" if os.environ.get("LLAMA_VERBOSE") else "warning",
    )
    _server = uvicorn.Server(config)
    _task = asyncio.create_task(_server.serve())
    _proxy_host = api.host
    _proxy_port = api.port
    _proxy_started_at = time.time()
    logger.info("Proxy server started on %s:%s", api.host, api.port)
    print(f"[proxy] started on {api.host}:{api.port}")


async def stop_proxy() -> None:
    global _server, _task, _proxy_host, _proxy_port, _proxy_started_at
    if _server is not None:
        _server.should_exit = True
    if _task is not None:
        await _task
    _server = None
    _task = None
    _proxy_host = None
    _proxy_port = None
    _proxy_started_at = None
    print("[proxy] stopped")


async def restart_proxy() -> None:
    await stop_proxy()
    await start_proxy()


def get_proxy_status() -> dict:
    uptime = None
    if _proxy_started_at is not None:
        uptime = time.time() - _proxy_started_at
    return {
        "state": "running" if _server is not None else "stopped",
        "host": _proxy_host,
        "port": _proxy_port,
        "uptime": uptime,
        "pid": os.getpid() if _server is not None else None,
    }
