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
from .log_buffer import LogBuffer

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
    """Send None sentinel to all subscriber queues so WS handlers exit."""
    for q in list(_proxy_subscribers):
        try:
            q.put_nowait(None)
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

# ---------------------------------------------------------------------------
# JIT model server start
# ---------------------------------------------------------------------------
from .process_manager import ProcessManager

_process_managers: list[ProcessManager] = []


def set_process_managers(pms: list[ProcessManager]) -> None:
    global _process_managers
    _process_managers = pms


async def _ensure_model_server(model_index: int = 0) -> None:
    """Start model server on-demand if JIT is enabled and server isn't running."""
    cfg = load_config()
    if not cfg.api_server.jit_model_server:
        return
    if model_index < 0 or model_index >= len(_process_managers):
        return
    pm = _process_managers[model_index]
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

    raise RuntimeError(f"Model server [{model_index}] did not become ready within {timeout}s")


def _resolve_model_index(model_id: str | None) -> int | None:
    """Resolve a model ID to a model index. Returns None if not found."""
    if not model_id:
        return 0
    cfg = load_config()
    for i, m in enumerate(cfg.models):
        if m.effective_id == model_id:
            return i
    return None


def _resolve_backend(model_id: str | None) -> str | None:
    """Resolve a model ID to a backend URL. Returns None if not found."""
    idx = _resolve_model_index(model_id)
    if idx is None:
        return None
    cfg = load_config()
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port + idx}"


def _default_backend() -> str:
    cfg = load_config()
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port}"


def _resolve_server_name(model_id: str | None) -> str:
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
    200: "OK", 201: "Created", 204: "No Content",
    400: "Bad Request", 404: "Not Found", 422: "Unprocessable Entity",
    500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
}


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    return f"{n / 1024:.1f}KB"


def _log_req(
    server_name: str | None, method: str, path: str,
    http_ver: str = "1.1", size: int | None = None,
) -> None:
    route = f"[{server_name}]" if server_name else ""
    msg = f"{method} {path} HTTP/{http_ver}"
    if size is not None:
        msg += f" [{_fmt_size(size)}]"
    _proxy_log(f"\u2192 {route} {msg}" if route else f"\u2192 {msg}")


def _log_resp(
    server_name: str | None, status: int, http_ver: str = "1.1",
    *, streaming: bool = False, elapsed: float | None = None,
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


def _log_stream_end(
    server_name: str | None, elapsed: float, size: int,
) -> None:
    route = f"[{server_name}]" if server_name else ""
    msg = f"stream complete ({elapsed:.2f}s) [{_fmt_size(size)}]"
    _proxy_log(f"\u2190 {route} {msg}" if route else f"\u2190 {msg}")


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
    body: dict, backend: str, model: str, t0: float, server_name: str,
) -> StreamingResponse:
    """Translate an OpenAI SSE stream into Anthropic SSE events."""
    oai_body = _anthropic_to_openai(body)

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
        yield _emit(_sse(
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
        ))

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{backend}/v1/chat/completions",
                    json=oai_body,
                    timeout=None,
                ) as resp:
                    _log_resp(server_name, resp.status_code, streaming=True, elapsed=time.monotonic() - t0)
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
                                yield _emit(_sse(
                                    "content_block_start",
                                    {
                                        "type": "content_block_start",
                                        "index": 0,
                                        "content_block": {"type": "text", "text": ""},
                                    },
                                ))
                                block_started = True
                            yield _emit(_sse(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": text},
                                },
                            ))
        except httpx.ConnectError:
            _log_resp(server_name, 502, elapsed=time.monotonic() - t0)
            yield _emit(_sse(
                "error",
                {"type": "error", "error": {"type": "server_error", "message": "Backend unavailable"}},
            ))
            return

        if block_started:
            yield _emit(_sse(
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ))

        yield _emit(_sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": finish_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        ))
        yield _emit(_sse("message_stop", {"type": "message_stop"}))

        _log_stream_end(server_name, time.monotonic() - t0, total_bytes)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _handle_anthropic(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    model = body.get("model", "unknown")
    model_id = body.get("model")
    http_ver = request.scope.get("http_version", "1.1")
    req_size = int(request.headers.get("content-length", 0)) or None
    model_index = _resolve_model_index(model_id)
    backend = _resolve_backend(model_id)
    if backend is None or model_index is None:
        _log_req(None, "POST", "/v1/messages", http_ver, req_size)
        _log_resp(None, 404)
        return JSONResponse(
            {"type": "error", "error": {"type": "not_found", "message": f"Model not found: {model}"}},
            status_code=404,
        )

    server_name = _resolve_server_name(model_id)
    _log_req(server_name, "POST", "/v1/messages", http_ver, req_size)
    t0 = time.monotonic()

    try:
        await _ensure_model_server(model_index)
    except RuntimeError as exc:
        _log_resp(server_name, 503, elapsed=time.monotonic() - t0)
        return JSONResponse(
            {"type": "error", "error": {"type": "server_error", "message": str(exc)}},
            status_code=503,
        )

    if body.get("stream"):
        return await _handle_anthropic_stream(body, backend, model, t0, server_name)

    oai_body = _anthropic_to_openai(body)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{backend}/v1/chat/completions",
                json=oai_body,
                timeout=None,
            )
        elapsed = time.monotonic() - t0
        oai_resp = resp.json()
        _log_resp(server_name, 200, elapsed=elapsed, size=len(resp.content))
        return JSONResponse(_openai_to_anthropic(oai_resp, model))
    except httpx.ConnectError:
        elapsed = time.monotonic() - t0
        _log_resp(server_name, 502, elapsed=elapsed)
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
    http_ver = request.scope.get("http_version", "1.1")
    req_size = int(request.headers.get("content-length", 0)) or None
    t0 = time.monotonic()
    server_name: str | None = None

    try:
        if method == "POST":
            body = await request.json()
            model_id = body.get("model")

            model_index = _resolve_model_index(model_id)
            backend = _resolve_backend(model_id)
            if backend is None or model_index is None:
                _log_req(None, method, f"/v1/{path}", http_ver, req_size)
                _log_resp(None, 404)
                return JSONResponse(
                    {"error": {"message": f"Model not found: {model_id}", "type": "not_found"}},
                    status_code=404,
                )

            server_name = _resolve_server_name(model_id)
            _log_req(server_name, method, f"/v1/{path}", http_ver, req_size)

            try:
                await _ensure_model_server(model_index)
            except RuntimeError as exc:
                _log_resp(server_name, 503, elapsed=time.monotonic() - t0)
                return JSONResponse(
                    {"error": {"message": str(exc), "type": "server_error"}},
                    status_code=503,
                )

            # Streaming SSE passthrough
            if body.get("stream"):
                return await _stream_passthrough(backend, path, body, t0, server_name)

            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    f"{backend}/v1/{path}",
                    json=body,
                    timeout=None,
                )
            elapsed = time.monotonic() - t0
            _log_resp(server_name, resp.status_code, elapsed=elapsed, size=len(resp.content))
            return JSONResponse(resp.json(), status_code=resp.status_code)
        else:
            backend = _default_backend()
            server_name = _resolve_server_name(None)
            _log_req(server_name, method, f"/v1/{path}", http_ver)

            try:
                await _ensure_model_server(0)
            except RuntimeError as exc:
                _log_resp(server_name, 503, elapsed=time.monotonic() - t0)
                return JSONResponse(
                    {"error": {"message": str(exc), "type": "server_error"}},
                    status_code=503,
                )

            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    f"{backend}/v1/{path}",
                    timeout=None,
                )
            elapsed = time.monotonic() - t0
            _log_resp(server_name, resp.status_code, elapsed=elapsed, size=len(resp.content))
            return JSONResponse(resp.json(), status_code=resp.status_code)

    except httpx.ConnectError:
        elapsed = time.monotonic() - t0
        _log_resp(server_name, 502, elapsed=elapsed)
        return JSONResponse(
            {"error": {"message": "Backend llama-server is not running", "type": "server_error"}},
            status_code=502,
        )


async def _stream_passthrough(backend: str, path: str, body: dict, t0: float, server_name: str) -> StreamingResponse:
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
                    _log_resp(server_name, resp.status_code, streaming=True, elapsed=time.monotonic() - t0)
                    async for line in resp.aiter_lines():
                        chunk = line + "\n"
                        total_bytes += len(chunk.encode())
                        yield chunk
            _log_stream_end(server_name, time.monotonic() - t0, total_bytes)
        except httpx.ConnectError:
            _log_resp(server_name, 502, elapsed=time.monotonic() - t0)
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
    _proxy_log(f"Proxy started on {api.host}:{api.port}")
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
    _proxy_log("Proxy stopped")
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
