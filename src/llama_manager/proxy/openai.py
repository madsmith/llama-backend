from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.config import AppConfig
from llama_manager.kv_cache import (
    CacheHit,
    CacheMiss,
    KVCacheProvider,
    SlotAvailabilityProvider,
    resolve_slot_save_path,
)
from llama_manager.model.identifier import ModelIdentifier

from .active_requests import ActiveRequestManager
from .lifecycle import ensure_model_server, touch_model
from .logging import log_request, log_response, log_stream_end
from .models import (
    BACKEND_ERRORS,
    backend_error_msg,
    default_backend,
    resolve_backend,
    resolve_model_index,
    resolve_server_name,
    rewrite_model_field,
)
from .request_log import RequestLog
from .slots import slot_restore, slot_save
from .translate import normalize_messages

logger = logging.getLogger(__name__)
request_log = RequestLog.get_instance()


class OpenAIProxy:
    def __init__(self, manager: LlamaManager) -> None:
        self._manager = manager
        self._config: AppConfig = manager.config
        self._slot_status = manager.slot_status


    async def __call__(self, path: str, request: Request):
        method = request.method
        http_ver = request.scope.get("http_version", "1.1")
        req_size = int(request.headers.get("content-length", 0)) or None
        t0 = time.monotonic()
        server_name: str | None = None
        request_id: str | None = getattr(request.state, "request_id", None)

        try:
            if method == "POST":
                body = await request.json()
                model_id = body.get("model")

                model_index = resolve_model_index(model_id, self._config)
                backend = resolve_backend(model_id, self._config)
                if model_index is not None:
                    self._slot_status.mark_active(model_index)
                if backend is None or model_index is None:
                    log_request(None, method, f"/v1/{path}", http_ver, req_size, request_id=request_id)
                    log_response(None, 404, request_id=request_id)
                    resp_body = {
                        "error": {
                            "message": f"Model not found: {model_id}",
                            "type": "not_found",
                        }
                    }
                    if request_id:
                        request_log.update(request_id, response_status=404, response_body=resp_body, elapsed=time.monotonic() - t0)
                    return JSONResponse(resp_body, status_code=404)

                body = normalize_messages(body, model_index)
                server_name = resolve_server_name(model_id, self._config)
                log_request(server_name, method, f"/v1/{path}", http_ver, req_size, request_id=request_id)

                try:
                    await ensure_model_server(model_index, self._config)
                except RuntimeError as exc:
                    log_response(server_name, 503, elapsed=time.monotonic() - t0, request_id=request_id)
                    resp_body = {"error": {"message": str(exc), "type": "server_error"}}
                    if request_id:
                        request_log.update(request_id, response_status=503, response_body=resp_body, elapsed=time.monotonic() - t0)
                    return JSONResponse(resp_body, status_code=503)

                touch_model(model_index)
                body = rewrite_model_field(body, model_id, self._config)

                # ----- KV cache logic -----
                slot_dir = resolve_slot_save_path(self._config, model_index)
                kv = None
                result = None  # CacheHit | CacheMiss | CacheInvalid
                slot_id = None
                slots = None

                if slot_dir is not None:
                    kv = KVCacheProvider.get(slot_dir)
                    slots = SlotAvailabilityProvider.get(
                        model_index,
                        self._config.models[model_index].parallel,
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
                    return await self._stream_passthrough(
                        backend,
                        path,
                        body,
                        t0,
                        server_name,
                        request,
                        model_index=model_index,
                        cache_save=cs,
                        request_id=request_id,
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
                    log_response(
                        server_name,
                        resp.status_code,
                        elapsed=elapsed,
                        size=len(resp.content),
                        request_id=request_id,
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

                    resp_json = resp.json()
                    if request_id:
                        request_log.update(request_id, response_status=resp.status_code, response_body=resp_json, elapsed=elapsed)
                    return JSONResponse(resp_json, status_code=resp.status_code)
                finally:
                    if slot_id is not None and slots is not None:
                        cache_id = (
                            result.get_cache_id()
                            if isinstance(result, (CacheHit, CacheMiss))
                            else None
                        )
                        await slots.free(slot_id, cache_id)
            else:
                backend = default_backend(self._config)
                server_name = resolve_server_name(None, self._config)
                log_request(server_name, method, f"/v1/{path}", http_ver, request_id=request_id)

                try:
                    await ensure_model_server(0, self._config)
                except RuntimeError as exc:
                    log_response(server_name, 503, elapsed=time.monotonic() - t0, request_id=request_id)
                    resp_body = {"error": {"message": str(exc), "type": "server_error"}}
                    if request_id:
                        request_log.update(request_id, response_status=503, response_body=resp_body, elapsed=time.monotonic() - t0)
                    return JSONResponse(resp_body, status_code=503)

                touch_model(0)
                async with httpx.AsyncClient() as client:
                    resp = await client.request(
                        method,
                        f"{backend}/v1/{path}",
                        timeout=None,
                    )
                elapsed = time.monotonic() - t0
                log_response(
                    server_name, resp.status_code, elapsed=elapsed, size=len(resp.content),
                    request_id=request_id,
                )
                resp_json = resp.json()
                if request_id:
                    request_log.update(request_id, response_status=resp.status_code, response_body=resp_json, elapsed=elapsed)
                return JSONResponse(resp_json, status_code=resp.status_code)

        except BACKEND_ERRORS as exc:
            elapsed = time.monotonic() - t0
            msg = backend_error_msg(exc)
            log_response(server_name, 502, elapsed=elapsed, request_id=request_id)
            self._manager.proxy.log(f"[{server_name}] {msg}", request_id=request_id)
            resp_body = {"error": {"message": msg, "type": "server_error"}}
            if request_id:
                request_log.update(request_id, response_status=502, response_body=resp_body, elapsed=elapsed)
            return JSONResponse(resp_body, status_code=502)

    async def _stream_passthrough(
        self,
        backend: str,
        path: str,
        body: dict,
        t0: float,
        server_name: str,
        request: Request,
        model_index: int | None = None,
        cache_save: tuple | None = None,
        request_id: str | None = None,
    ) -> StreamingResponse:
        """Stream SSE passthrough.

        model_index: enables cancellation via the active-request registry.
        cache_save: optional (kv, cache_id, slot_id, slots) tuple for post-stream save.
        """
        active_slot_id = body.get("id_slot")
        cancel_event = None
        if model_index is not None and active_slot_id is not None:
            cancel_event = ActiveRequestManager.register(model_index, active_slot_id)

        async def _resolve_slot() -> int | None:
            """Poll /slots to find which slot picked up our request."""
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    response = await client.get(f"{backend}/slots")
                    if response.status_code == 200:
                        for slot in response.json():
                            if slot.get("is_processing"):
                                return slot.get("id")
            except Exception:
                pass
            return None

        async def response_generator():
            nonlocal active_slot_id, cancel_event
            total_bytes = 0
            stream_ok = False
            accumulated_text = ""
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        f"{backend}/v1/{path}",
                        json=body,
                        timeout=None,
                    ) as resp:
                        log_response(
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
                                cancel_event = ActiveRequestManager.register(model_index, active_slot_id)
                        async for line in resp.aiter_lines():
                            if cancel_event is not None and cancel_event.is_set():
                                self._manager.proxy.log(
                                    f"\u2190 [{server_name}] slot {active_slot_id} cancelled by operator"
                                )
                                error = json.dumps(
                                    {
                                        "error": {
                                            "message": "Request cancelled: inference terminated by server operator",
                                            "type": "capacity_exceeded",
                                            "code": "capacity_exceeded",
                                        }
                                    }
                                )
                                yield f"data: {error}\n\n"
                                return
                            if await request.is_disconnected():
                                self._manager.proxy.log(
                                    f"\u2190 [{server_name}] client disconnected, aborting stream"
                                )
                                return
                            # Accumulate text content for request log
                            if request_id and line.startswith("data: "):
                                try:
                                    d = json.loads(line[6:])
                                    c = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
                                    if c:
                                        accumulated_text += c
                                except (json.JSONDecodeError, IndexError):
                                    pass
                            chunk = line + "\n"
                            total_bytes += len(chunk.encode())
                            yield chunk
                log_stream_end(server_name, time.monotonic() - t0, total_bytes, request_id=request_id)
                stream_ok = True
                if request_id:
                    request_log.update(
                        request_id,
                        response_body=accumulated_text,
                        response_status=200,
                        streaming=True,
                        elapsed=time.monotonic() - t0,
                    )
            except BACKEND_ERRORS as exc:
                msg = backend_error_msg(exc)
                log_response(server_name, 502, elapsed=time.monotonic() - t0, request_id=request_id)
                self._manager.proxy.log(f"[{server_name}] {msg}", request_id=request_id)
                error = json.dumps({"error": {"message": msg, "type": "server_error"}})
                yield f"data: {error}\n\n"
                if request_id:
                    request_log.update(
                        request_id,
                        response_status=502,
                        elapsed=time.monotonic() - t0,
                    )
            finally:
                if model_index is not None and active_slot_id is not None:
                    ActiveRequestManager.unregister(model_index, active_slot_id)

            if cache_save:
                kv, cache_id, slot_id, slots = cache_save
                if stream_ok:
                    if await slot_save(backend, slot_id, f"{cache_id}.bin"):
                        kv.record_save(cache_id, slot_id)
                await slots.free(slot_id, cache_id)

        return StreamingResponse(response_generator(), media_type="text/event-stream")