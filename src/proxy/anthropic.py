from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

from ..config import load_config
from ..kv_cache import (
    CacheHit,
    CacheMiss,
    KVCacheProvider,
    SlotAvailabilityProvider,
    resolve_slot_save_path,
)
from .active_requests import register as register_active
from .active_requests import unregister as unregister_active
from .lifecycle import ensure_model_server, touch_model
from .logging import log_req, log_resp, log_stream_end
from .models import (
    BACKEND_ERRORS,
    backend_error_msg,
    resolve_backend,
    resolve_model_index,
    resolve_server_name,
    rewrite_model_field,
)
from .request_log import request_log
from .slots import slot_restore, slot_save
from .subscription import proxy_log
from .translate import FINISH_MAP, anthropic_to_openai, openai_to_anthropic, sse

logger = logging.getLogger(__name__)


async def _stream_passthrough(
    backend: str,
    model: str,
    body: dict,
    t0: float,
    server_name: str,
    request: Request,
    oai_body: dict | None = None,
    model_index: int | None = None,
    cache_save: tuple | None = None,
    request_id: str | None = None,
) -> StreamingResponse:
    """Translate an OpenAI SSE stream into Anthropic SSE events.

    model_index: enables cancellation via the active-request registry.
    cache_save: optional (kv_cache, cache_id, slot_id, slots) tuple for post-stream save.
    """
    if oai_body is None:
        oai_body = anthropic_to_openai(body)

    active_slot_id = oai_body.get("id_slot")
    cancel_event = None
    if model_index is not None and active_slot_id is not None:
        cancel_event = register_active(model_index, active_slot_id)

    async def _resolve_slot() -> int | None:
        """Poll /slots to find which slot picked up our request."""
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(f"{backend}/slots")
                if r.status_code == 200:
                    for s in r.json():
                        if s.get("is_processing"):
                            return s.get("id")
        except Exception:
            pass
        return None

    async def generate():
        nonlocal active_slot_id, cancel_event
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        input_tokens = 0
        output_tokens = 0
        block_started = False
        finish_reason = "end_turn"
        total_bytes = 0
        accumulated_text = ""

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
                        request_id=request_id,
                    )
                    # If we don't already know the slot, try to discover it
                    if (
                        model_index is not None
                        and active_slot_id is None
                        and resp.status_code == 200
                    ):
                        resolved = await _resolve_slot()
                        if resolved is not None:
                            active_slot_id = resolved
                            cancel_event = register_active(model_index, active_slot_id)
                    async for line in resp.aiter_lines():
                        if cancel_event is not None and cancel_event.is_set():
                            proxy_log(
                                f"\u2190 [{server_name}] slot {active_slot_id} cancelled by operator"
                            )
                            yield _emit(
                                sse(
                                    "error",
                                    {
                                        "type": "error",
                                        "error": {
                                            "type": "overloaded",
                                            "message": "Request cancelled: inference terminated by server operator",
                                        },
                                    },
                                )
                            )
                            if model_index is not None and active_slot_id is not None:
                                unregister_active(model_index, active_slot_id)
                            return
                        if await request.is_disconnected():
                            proxy_log(
                                f"\u2190 [{server_name}] client disconnected, aborting stream"
                            )
                            if model_index is not None and active_slot_id is not None:
                                unregister_active(model_index, active_slot_id)
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
                            accumulated_text += text
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
            log_resp(server_name, 502, elapsed=time.monotonic() - t0, request_id=request_id)
            proxy_log(f"[{server_name}] {msg}", request_id=request_id)
            yield _emit(
                sse(
                    "error",
                    {
                        "type": "error",
                        "error": {"type": "server_error", "message": msg},
                    },
                )
            )
            if request_id:
                request_log.update(request_id, response_status=502, elapsed=time.monotonic() - t0)
            if model_index is not None and active_slot_id is not None:
                unregister_active(model_index, active_slot_id)
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

        log_stream_end(server_name, time.monotonic() - t0, total_bytes, request_id=request_id)
        if request_id:
            request_log.update(
                request_id,
                response_body=accumulated_text,
                response_status=200,
                streaming=True,
                elapsed=time.monotonic() - t0,
            )

        if model_index is not None and active_slot_id is not None:
            unregister_active(model_index, active_slot_id)

        if cache_save:
            kv, cache_id, sid, sa = cache_save
            if await slot_save(backend, sid, f"{cache_id}.bin"):
                kv.record_save(cache_id, sid)
            await sa.free(sid, cache_id)

    return StreamingResponse(generate(), media_type="text/event-stream")


async def handle_anthropic(request: Request) -> JSONResponse | StreamingResponse:
    body = await request.json()
    model = body.get("model", "unknown")
    model_id = body.get("model")
    http_ver = request.scope.get("http_version", "1.1")
    req_size = int(request.headers.get("content-length", 0)) or None
    request_id: str | None = getattr(request.state, "request_id", None)
    model_index = resolve_model_index(model_id)
    backend = resolve_backend(model_id)
    if backend is None or model_index is None:
        log_req(None, "POST", "/v1/messages", http_ver, req_size, request_id=request_id)
        log_resp(None, 404, request_id=request_id)
        resp_body = {
            "type": "error",
            "error": {"type": "not_found", "message": f"Model not found: {model}"},
        }
        if request_id:
            request_log.update(request_id, response_status=404, response_body=resp_body, elapsed=0.0)
        return JSONResponse(resp_body, status_code=404)

    server_name = resolve_server_name(model_id)
    log_req(server_name, "POST", "/v1/messages", http_ver, req_size, request_id=request_id)
    t0 = time.monotonic()

    try:
        await ensure_model_server(model_index)
    except RuntimeError as exc:
        log_resp(server_name, 503, elapsed=time.monotonic() - t0, request_id=request_id)
        resp_body = {"type": "error", "error": {"type": "server_error", "message": str(exc)}}
        if request_id:
            request_log.update(request_id, response_status=503, response_body=resp_body, elapsed=time.monotonic() - t0)
        return JSONResponse(resp_body, status_code=503)

    touch_model(model_index)
    body = rewrite_model_field(body, model_id)

    # ----- KV cache logic (operates on translated OAI messages) -----
    oai_body = anthropic_to_openai(body)
    cfg = load_config()
    slot_dir = resolve_slot_save_path(cfg, model_index)
    kv = None
    result = None  # CacheHit | CacheMiss | CacheInvalid
    slot_id = None
    slots = None

    if slot_dir is not None:
        kv = KVCacheProvider.get(slot_dir)
        slots = SlotAvailabilityProvider.get(
            model_index,
            cfg.models[model_index].parallel,
        )
        assert kv is not None
        assert slots is not None
        messages = oai_body.get("messages", [])
        result = kv.get(messages)
        # TODO - Remove
        logger.warning("KV cache: %s", type(result).__name__)

        if isinstance(result, (CacheHit, CacheMiss)):
            slot_id = await slots.get_available()
            if slot_id is None:
                # TODO - Remove
                logger.warning("KV cache: no slots available")
            elif isinstance(result, CacheHit):
                cache_id = result.get_cache_id()
                restored = await slot_restore(backend, slot_id, f"{cache_id}.bin")
                if restored:
                    kv.record_restore(cache_id, slot_id)
                    oai_body = {**oai_body, "id_slot": slot_id}
                else:
                    await slots.free(slot_id)
                    slot_id = None
            else:
                # TODO - Remove
                logger.warning("KV cache: miss, using slot %d", slot_id)
                oai_body = {**oai_body, "id_slot": slot_id}

    if body.get("stream"):
        cs = (
            (kv, result.get_cache_id(), slot_id, slots)
            if isinstance(result, CacheMiss) and slot_id is not None
            else None
        )
        if slot_id is not None and slots is not None and cs is None:
            await slots.free(slot_id)
        return await _stream_passthrough(
            backend,
            model,
            body,
            t0,
            server_name,
            request,
            oai_body=oai_body,
            model_index=model_index,
            cache_save=cs,
            request_id=request_id,
        )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{backend}/v1/chat/completions",
                json=oai_body,
                timeout=None,
            )
        elapsed = time.monotonic() - t0
        oai_resp = resp.json()
        log_resp(server_name, 200, elapsed=elapsed, size=len(resp.content), request_id=request_id)

        # Save KV cache on non-streaming cache miss
        if (
            isinstance(result, CacheMiss)
            and slot_id is not None
            and resp.status_code == 200
        ):
            assert kv is not None
            cache_id = result.get_cache_id()
            if await slot_save(backend, slot_id, f"{cache_id}.bin"):
                kv.record_save(cache_id, slot_id)

        anthropic_resp = openai_to_anthropic(oai_resp, model)
        if request_id:
            request_log.update(request_id, response_status=200, response_body=anthropic_resp, elapsed=elapsed)
        return JSONResponse(anthropic_resp)
    except BACKEND_ERRORS as exc:
        elapsed = time.monotonic() - t0
        msg = backend_error_msg(exc)
        log_resp(server_name, 502, elapsed=elapsed, request_id=request_id)
        proxy_log(f"[{server_name}] {msg}", request_id=request_id)
        resp_body = {
            "type": "error",
            "error": {"type": "server_error", "message": msg},
        }
        if request_id:
            request_log.update(request_id, response_status=502, response_body=resp_body, elapsed=elapsed)
        return JSONResponse(resp_body, status_code=502)
    finally:
        if slot_id is not None and slots is not None:
            cache_id = (
                result.get_cache_id()
                if isinstance(result, (CacheHit, CacheMiss))
                else None
            )
            await slots.free(slot_id, cache_id)
