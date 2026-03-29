from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, AsyncGenerator, AsyncIterator, Awaitable, Callable, Protocol

import httpx
from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager
    from llama_manager.protocol.backend import Backend
    from .server import ProxyServer
from llama_manager.config import ModelConfig
from llama_manager.kv_cache import (
    CacheHit,
    CacheMiss,
    KVCacheProvider,
    SlotAvailabilityProvider,
)

from .active_requests import ActiveRequestManager
from .logging import log_request, log_response, log_stream_end
from .slots import slot_restore, slot_save

logger = logging.getLogger(__name__)


class ProtocolAdapter(Protocol):
    """Pluggable protocol layer for ProxyHandler.

    Implementations translate between the wire protocol (OpenAI, Anthropic,
    etc.) and the OAI-compatible format that llama-server speaks.
    """

    def prepare_body(self, body: dict, model_config: ModelConfig | None) -> dict:
        """Translate/normalise incoming request body before forwarding."""
        ...

    def translate_response(self, resp_json: dict) -> dict:
        """Translate non-streaming backend response to protocol response."""
        ...

    def error_body(self, status: int, msg: str) -> dict:
        """Build a protocol-appropriate error response body."""
        ...

    def backend_error_sse(self, msg: str) -> str:
        """SSE payload to emit when the backend returns a transport error."""
        ...

    async def wrap_stream(
        self,
        lines: AsyncIterator[str],
        is_cancelled: Callable[[], bool],
        is_disconnected: Callable[[], Awaitable[bool]],
        on_content: Callable[[str], None],
    ) -> AsyncGenerator[str, None]:
        """Yield protocol SSE output from raw upstream SSE lines.

        The adapter is responsible for:
        - Checking is_cancelled() / await is_disconnected() each iteration
        - Emitting the appropriate cancel/disconnect response and returning
        - Translating upstream lines into protocol-specific SSE events
        - Calling on_content(text) for each piece of assistant text (for logging)
        """
        ...


class ProxyHandler:
    """Protocol-agnostic inference proxy.

    Owns backend resolution, JIT server start, KV cache, request/stream
    lifecycle, and logging.  Delegates protocol-specific translation to a
    ProtocolAdapter.
    """

    _BACKEND_ERRORS = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)

    @staticmethod
    def _backend_error_msg(exc: Exception) -> str:
        if isinstance(exc, httpx.ConnectError):
            return "Backend server is not reachable"
        return "Backend server disconnected"

    def __init__(self, manager: LlamaManager, adapter: ProtocolAdapter, proxy: ProxyServer) -> None:
        self._manager = manager
        self._adapter = adapter
        self._proxy = proxy

    @staticmethod
    def _rewrite_body(body: dict, model_id: str | None, backend: Backend) -> dict:
        return {**body, "model": backend.map_model_id(model_id)}

    async def __call__(self, path: str, request: Request) -> JSONResponse | StreamingResponse:
        return await self.handle(path, request)

    async def handle(self, path: str, request: Request) -> JSONResponse | StreamingResponse:
        method = request.method
        http_ver = request.scope.get("http_version", "1.1")
        req_size = int(request.headers.get("content-length", 0)) or None
        t0 = time.monotonic()
        server_name: str | None = None
        request_id: str | None = getattr(request.state, "request_id", None)
        adapter = self._adapter

        try:
            if method == "POST":
                body = await request.json()
                model_id = body.get("model")

                backend = self._manager.find_backend(model_id)
                if backend is None:
                    log_request(None, method, f"/v1/{path}", http_ver, req_size, request_id=request_id)
                    log_response(None, 404, request_id=request_id)
                    resp_body = adapter.error_body(404, f"Model not found: {model_id}")
                    if request_id:
                        self._proxy.request_log.update(request_id, response_status=404, response_body=resp_body, elapsed=time.monotonic() - t0)
                    return JSONResponse(resp_body, status_code=404)

                suid = backend.get_suid()
                model_config = self._manager.get_model_config(suid)
                self._manager.slot_status.mark_active(suid)

                body = adapter.prepare_body(body, model_config)
                server_name = backend.get_name() or model_id
                log_request(server_name, method, f"/v1/{path}", http_ver, req_size, request_id=request_id)

                try:
                    await self._manager.ensure_server(backend)
                except RuntimeError as exc:
                    log_response(server_name, 503, elapsed=time.monotonic() - t0, request_id=request_id)
                    resp_body = adapter.error_body(503, str(exc))
                    if request_id:
                        self._proxy.request_log.update(request_id, response_status=503, response_body=resp_body, elapsed=time.monotonic() - t0)
                    return JSONResponse(resp_body, status_code=503)

                self._manager.touch(suid)
                body = self._rewrite_body(body, model_id, backend)

                # ----- KV cache logic -----
                slot_dir = self._manager.get_slot_save_path(suid)
                kv = None
                result = None  # CacheHit | CacheMiss | CacheInvalid
                slot_id = None
                slots = None

                if slot_dir is not None and model_config is not None:
                    kv = KVCacheProvider.get(slot_dir)
                    slots = SlotAvailabilityProvider.get(suid, model_config.parallel)
                    messages = body.get("messages", [])
                    result = kv.get(messages)
                    logger.warning("KV cache: %s", type(result).__name__)

                    if isinstance(result, (CacheHit, CacheMiss)):
                        slot_id = await slots.get_available()
                        if slot_id is None:
                            logger.warning("KV cache: no slots available")
                        elif isinstance(result, CacheHit):
                            cache_id = result.get_cache_id()
                            restored = await slot_restore(backend.get_base_url(), slot_id, f"{cache_id}.bin")
                            if restored:
                                kv.record_restore(cache_id, slot_id)
                                body = {**body, "id_slot": slot_id}
                            else:
                                await slots.free(slot_id)
                                slot_id = None
                        else:
                            logger.warning("KV cache: miss, using slot %d", slot_id)
                            body = {**body, "id_slot": slot_id}

                backend_url = backend.get_base_url()

                # Streaming SSE
                if body.get("stream"):
                    cs = None
                    if isinstance(result, CacheMiss) and slot_id is not None:
                        assert kv is not None and slots is not None
                        cs = (kv, result.get_cache_id(), slot_id, slots)
                    elif slot_id is not None and slots is not None:
                        await slots.free(slot_id)
                    return await self._stream(
                        backend_url, path, body, t0, server_name, request,
                        suid=suid, cache_save=cs, request_id=request_id,
                    )

                active_slot_id = body.get("id_slot")
                cancel_event: asyncio.Event | None = None
                if suid is not None and active_slot_id is not None:
                    cancel_event = ActiveRequestManager.register(suid, active_slot_id)

                resolve_lock: asyncio.Lock | None = None
                lock_held = False
                if suid is not None and active_slot_id is None:
                    resolve_lock = self._proxy.get_resolve_lock(suid)
                    await resolve_lock.acquire()
                    lock_held = True

                try:
                    async with httpx.AsyncClient() as client:
                        req_task = asyncio.create_task(
                            client.request(method, f"{backend_url}/v1/{path}", json=body, timeout=None)
                        )

                        async def _wait_disconnect() -> None:
                            while True:
                                msg = await request._receive()
                                if msg.get("type") == "http.disconnect":
                                    return

                        disc_task = asyncio.create_task(_wait_disconnect())

                        if lock_held:
                            try:
                                claimed = await self._resolve_and_register_slot(backend_url, suid)
                                if claimed is not None:
                                    active_slot_id, cancel_event = claimed
                            finally:
                                resolve_lock.release()
                                lock_held = False

                        tasks: list[asyncio.Task] = [req_task, disc_task]
                        cancel_task: asyncio.Task | None = None
                        if cancel_event is not None:
                            cancel_task = asyncio.create_task(cancel_event.wait())
                            tasks.append(cancel_task)

                        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for t in pending:
                            t.cancel()
                        if disc_task in done:
                            log_response(server_name, 499, elapsed=time.monotonic() - t0, request_id=request_id)
                            if request_id:
                                self._proxy.request_log.update(request_id, response_status=499, elapsed=time.monotonic() - t0)
                            return JSONResponse(adapter.error_body(503, "Client disconnected"), status_code=503)
                        if cancel_task is not None and cancel_task in done:
                            log_response(server_name, 503, elapsed=time.monotonic() - t0, request_id=request_id)
                            if request_id:
                                self._proxy.request_log.update(request_id, response_status=503, elapsed=time.monotonic() - t0)
                            return JSONResponse(adapter.error_body(503, "Request cancelled: inference terminated by server operator"), status_code=503)
                        resp = req_task.result()

                    elapsed = time.monotonic() - t0
                    log_response(server_name, resp.status_code, elapsed=elapsed, size=len(resp.content), request_id=request_id)

                    if isinstance(result, CacheMiss) and slot_id is not None and resp.status_code == 200:
                        assert kv is not None
                        cache_id = result.get_cache_id()
                        if await slot_save(backend_url, slot_id, f"{cache_id}.bin"):
                            kv.record_save(cache_id, slot_id)

                    resp_json = adapter.translate_response(resp.json())
                    if request_id:
                        self._proxy.request_log.update(request_id, response_status=resp.status_code, response_body=resp_json, elapsed=elapsed)
                    return JSONResponse(resp_json, status_code=resp.status_code)
                finally:
                    if lock_held and resolve_lock is not None:
                        resolve_lock.release()
                    if active_slot_id is not None and suid is not None:
                        ActiveRequestManager.unregister(suid, active_slot_id)
                    if slot_id is not None and slots is not None:
                        cache_id = result.get_cache_id() if isinstance(result, (CacheHit, CacheMiss)) else None
                        await slots.free(slot_id, cache_id)

            else:
                backend = self._manager.find_backend(None)
                if backend is None:
                    log_request(None, method, f"/v1/{path}", http_ver, request_id=request_id)
                    log_response(None, 503, elapsed=0.0, request_id=request_id)
                    resp_body = adapter.error_body(503, "No backend available")
                    return JSONResponse(resp_body, status_code=503)

                backend_url = backend.get_base_url()
                server_name = backend.get_name()
                log_request(server_name, method, f"/v1/{path}", http_ver, request_id=request_id)

                try:
                    await self._manager.ensure_server(backend)
                except RuntimeError as exc:
                    log_response(server_name, 503, elapsed=time.monotonic() - t0, request_id=request_id)
                    resp_body = adapter.error_body(503, str(exc))
                    if request_id:
                        self._proxy.request_log.update(request_id, response_status=503, response_body=resp_body, elapsed=time.monotonic() - t0)
                    return JSONResponse(resp_body, status_code=503)

                self._manager.touch(backend.get_suid())
                async with httpx.AsyncClient() as client:
                    resp = await client.request(method, f"{backend_url}/v1/{path}", timeout=None)
                elapsed = time.monotonic() - t0
                log_response(server_name, resp.status_code, elapsed=elapsed, size=len(resp.content), request_id=request_id)
                resp_json = resp.json()
                if request_id:
                    self._proxy.request_log.update(request_id, response_status=resp.status_code, response_body=resp_json, elapsed=elapsed)
                return JSONResponse(resp_json, status_code=resp.status_code)

        except ProxyHandler._BACKEND_ERRORS as exc:
            elapsed = time.monotonic() - t0
            msg = self._backend_error_msg(exc)
            log_response(server_name, 502, elapsed=elapsed, request_id=request_id)
            self._proxy.log(f"[{server_name}] {msg}", request_id=request_id)
            resp_body = adapter.error_body(502, msg)
            if request_id:
                self._proxy.request_log.update(request_id, response_status=502, response_body=resp_body, elapsed=elapsed)
            return JSONResponse(resp_body, status_code=502)

    @staticmethod
    async def _resolve_and_register_slot(
        backend_url: str, suid: str
    ) -> tuple[int, asyncio.Event] | None:
        """Poll /slots and atomically claim the first processing slot not yet registered.

        Iterates all processing slots and calls try_register on each until one succeeds,
        so concurrent requests cannot claim the same slot.
        Returns (slot_id, cancel_event) or None if no unclaimed processing slot found.
        """
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{backend_url}/slots")
                if response.status_code == 200:
                    for slot in response.json():
                        if slot.get("is_processing") and "id" in slot:
                            event = ActiveRequestManager.try_register(suid, slot["id"])
                            if event is not None:
                                return slot["id"], event
        except Exception:
            pass
        return None

    async def _stream(
        self,
        backend_url: str,
        path: str,
        body: dict,
        t0: float,
        server_name: str,
        request: Request,
        suid: str | None = None,
        cache_save: tuple | None = None,
        request_id: str | None = None,
    ) -> StreamingResponse:
        adapter = self._adapter
        active_slot_id = body.get("id_slot")
        cancel_event = None
        if suid is not None and active_slot_id is not None:
            cancel_event = ActiveRequestManager.register(suid, active_slot_id)

        resolve_lock: asyncio.Lock | None = None
        if suid is not None and active_slot_id is None:
            resolve_lock = self._proxy.get_resolve_lock(suid)
            await resolve_lock.acquire()

        async def response_generator():
            nonlocal active_slot_id, cancel_event
            lock_held = resolve_lock is not None
            total_bytes = 0
            stream_ok = False
            accumulated_text: list[str] = []

            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST", f"{backend_url}/v1/{path}", json=body, timeout=None,
                    ) as resp:
                        log_response(
                            server_name, resp.status_code, streaming=True,
                            elapsed=time.monotonic() - t0, request_id=request_id,
                        )

                        if lock_held:
                            try:
                                if suid is not None and active_slot_id is None and resp.status_code == 200:
                                    claimed = await self._resolve_and_register_slot(backend_url, suid)
                                    if claimed is not None:
                                        active_slot_id, cancel_event = claimed
                            finally:
                                resolve_lock.release()
                                lock_held = False

                        def is_cancelled() -> bool:
                            return cancel_event is not None and cancel_event.is_set()

                        async def is_disconnected() -> bool:
                            return await request.is_disconnected()

                        async def _abort_on_disconnect() -> None:
                            while True:
                                msg = await request._receive()
                                if msg.get("type") == "http.disconnect":
                                    await resp.aclose()
                                    return

                        abort_task = asyncio.create_task(_abort_on_disconnect())
                        try:
                            async for chunk in adapter.wrap_stream(
                                resp.aiter_lines(), is_cancelled, is_disconnected,
                                accumulated_text.append,
                            ):
                                total_bytes += len(chunk.encode())
                                yield chunk
                        finally:
                            abort_task.cancel()

                log_stream_end(server_name, time.monotonic() - t0, total_bytes, request_id=request_id)
                stream_ok = True
                if request_id:
                    self._proxy.request_log.update(
                        request_id,
                        response_body="".join(accumulated_text),
                        response_status=200,
                        streaming=True,
                        elapsed=time.monotonic() - t0,
                    )
            except ProxyHandler._BACKEND_ERRORS as exc:
                msg = self._backend_error_msg(exc)
                log_response(server_name, 502, elapsed=time.monotonic() - t0, request_id=request_id)
                self._proxy.log(f"[{server_name}] {msg}", request_id=request_id)
                yield adapter.backend_error_sse(msg)
                if request_id:
                    self._proxy.request_log.update(request_id, response_status=502, elapsed=time.monotonic() - t0)
            finally:
                if lock_held and resolve_lock is not None:
                    resolve_lock.release()
                if suid is not None and active_slot_id is not None:
                    ActiveRequestManager.unregister(suid, active_slot_id)

            if cache_save:
                kv, cache_id, slot_id, slots = cache_save
                if stream_ok:
                    if await slot_save(backend_url, slot_id, f"{cache_id}.bin"):
                        kv.record_save(cache_id, slot_id)
                await slots.free(slot_id, cache_id)

        return StreamingResponse(response_generator(), media_type="text/event-stream")
