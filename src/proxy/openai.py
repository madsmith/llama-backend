from __future__ import annotations

import json
import logging
import time

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
from .slots import slot_restore, slot_save
from .subscription import proxy_log
from .translate import normalize_messages

logger = logging.getLogger(__name__)


async def _stream_passthrough(
    backend: str,
    path: str,
    body: dict,
    t0: float,
    server_name: str,
    request: Request,
    cache_save: tuple | None = None,
) -> StreamingResponse:
    """Stream SSE passthrough.

    cache_save: optional (kv, cache_id, slot_id, slots) tuple for post-stream save.
    """

    async def generate():
        total_bytes = 0
        stream_ok = False
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
            stream_ok = True
        except BACKEND_ERRORS as exc:
            msg = backend_error_msg(exc)
            log_resp(server_name, 502, elapsed=time.monotonic() - t0)
            proxy_log(f"[{server_name}] {msg}")
            error = json.dumps({"error": {"message": msg, "type": "server_error"}})
            yield f"data: {error}\n\n"

        if cache_save:
            kv, cache_id, slot_id, slots = cache_save
            if stream_ok:
                if await slot_save(backend, slot_id, f"{cache_id}.bin"):
                    kv.record_save(cache_id, slot_id)
            await slots.free(slot_id, cache_id)

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

            # ----- KV cache logic -----
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
                messages = body.get("messages", [])
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
                        restored = await slot_restore(
                            backend, slot_id, f"{cache_id}.bin"
                        )
                        if restored:
                            kv.record_restore(cache_id, slot_id)
                            body = {**body, "id_slot": slot_id}
                        else:
                            await slots.free(slot_id)
                            slot_id = None
                    else:
                        # TODO - Remove
                        logger.warning("KV cache: miss, using slot %d", slot_id)
                        body = {**body, "id_slot": slot_id}

            # Streaming SSE passthrough
            if body.get("stream"):
                cs = None
                if isinstance(result, CacheMiss) and slot_id is not None:
                    assert kv is not None and slots is not None
                    cs = (kv, result.get_cache_id(), slot_id, slots)
                elif slot_id is not None and slots is not None:
                    await slots.free(slot_id)
                return await _stream_passthrough(
                    backend, path, body, t0, server_name, request, cache_save=cs
                )

            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(
                        method,
                        f"{backend}/v1/{path}",
                        json=body,
                        timeout=None,
                    )
                elapsed = time.monotonic() - t0
                log_resp(
                    server_name,
                    resp.status_code,
                    elapsed=elapsed,
                    size=len(resp.content),
                )

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

                return JSONResponse(resp.json(), status_code=resp.status_code)
            finally:
                if slot_id is not None and slots is not None:
                    cache_id = (
                        result.get_cache_id()
                        if isinstance(result, (CacheHit, CacheMiss))
                        else None
                    )
                    await slots.free(slot_id, cache_id)
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
