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
from starlette.responses import Response
from typing import Any, overload

from typing import TYPE_CHECKING

from llama_manager.config import AppConfig
from llama_manager.util.log_buffer import LogBuffer, LogRecordData, ProxyRequest, ProxyResponse
from llama_manager.protocol.ws_messages import LogRecord as WireLogRecord

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.manager.backends.remote_proxy import RemoteModelProxy
from .handler import ProxyHandler
from .openai import OpenAIAdapter
from .request_log import RequestLog
logger = logging.getLogger(__name__)


class ProxyServer:
    def __init__(self, manager: LlamaManager) -> None:
        self._manager = manager
        self.config: AppConfig = manager.config
        self.log_buffer = LogBuffer(manager.get_manager_id(), maxlen=self.config.web_ui.log_buffer_size)
        self.request_log = RequestLog()

        self.app = FastAPI(title="Llama Proxy")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.app.middleware("http")(self._request_id_middleware)
        self._slot_resolve_locks: dict[str, asyncio.Lock] = {}

        self.app.api_route(
            "/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            response_model=None,
        )(self._openai_handle())

        _methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]

        async def _proxy_root(suid: str, request: Request) -> Response:
            return await self._direct_proxy(suid, "", request)

        async def _proxy_path(suid: str, path: str, request: Request) -> Response:
            return await self._direct_proxy(suid, path, request)

        self.app.api_route("/proxy/{suid}", methods=_methods, response_model=None)(_proxy_root)
        self.app.api_route("/proxy/{suid}/{path:path}", methods=_methods, response_model=None)(_proxy_path)

        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._host: str = self.config.api_server.host
        self._port: int = self.config.api_server.port
        self._started_at: float | None = None

    def get_log_buffer(self) -> LogBuffer:
        return self.log_buffer

    def enable_save_logs(self) -> None:
        self.request_log.enable_save_logs()

    def get_request(self, request_id: str):
        return self.request_log.get(request_id)

    def list_requests(self) -> list:
        result = []
        for entry in self.request_log.list_entries():
            d = entry.to_dict()
            d["response_body"] = self._truncate_body(d.get("response_body"))
            d["request_body"] = self._truncate_body(d.get("request_body"))
            result.append(d)
        return result

    def get_resolve_lock(self, suid: str) -> asyncio.Lock:
        if suid not in self._slot_resolve_locks:
            self._slot_resolve_locks[suid] = asyncio.Lock()
        return self._slot_resolve_locks[suid]

    def _openai_handle(self) -> ProxyHandler:
        return ProxyHandler(self._manager, OpenAIAdapter(), self)

    async def _direct_proxy(self, suid: str, path: str, request: Request) -> Response:
        model_config = next((m for m in self._manager.config.models if m.suid == suid), None)
        if model_config is not None and not model_config.allow_proxy:
            return Response(
                content=json.dumps({"error": "Proxy access disabled for this model"}),
                status_code=403,
                media_type="application/json",
            )

        backend = (
            self._manager.get_local_models().get(suid)
            or self._manager.get_remote_unmanaged().get(suid)
            or next((p for p in self._manager.get_remote_models() if p.get_suid() == suid), None)
        )
        if backend is None:
            return Response(
                content=json.dumps({"error": "Backend not found"}),
                status_code=404,
                media_type="application/json",
            )

        base_url = backend.get_base_url().rstrip("/")
        if isinstance(backend, RemoteModelProxy):
            target_url = f"{base_url}/proxy/{suid}/{path}" if path else f"{base_url}/proxy/{suid}/"
        else:
            target_url = f"{base_url}/{path}" if path else base_url
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        _skip_req = {"host", "content-length", "transfer-encoding", "accept-encoding"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _skip_req}

        _skip_resp = {"content-length", "transfer-encoding", "content-encoding"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                upstream = await client.request(
                    method=request.method,
                    url=target_url,
                    content=request.state.raw_body,
                    headers=headers,
                )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
                headers={k: v for k, v in upstream.headers.items() if k.lower() not in _skip_resp},
            )
        except httpx.ConnectError:
            return Response(
                content=json.dumps({"error": "Backend unavailable"}),
                status_code=503,
                media_type="application/json",
            )
        except Exception:
            return Response(
                content=json.dumps({"error": "Proxy error"}),
                status_code=502,
                media_type="application/json",
            )

    @overload
    def log(self, data: str, *, request_id: str | None = None) -> None: 
        """Log a string message with optional request_id."""
        ...  # TODO: migrate callers to typed ProxyRequest/ProxyResponse

    @overload
    def log(self, data: ProxyRequest | ProxyResponse) -> None:
        """Log a ProxyRequest or ProxyResponse object."""
        ...

    def log(self, data: LogRecordData, *, request_id: str | None = None) -> None:
        if isinstance(data, str):
            data = f"[{time.strftime('%H:%M:%S')}] {data}"
        line = self.log_buffer.append(data, request_id=request_id)
        self._manager.event_bus.publish({
            "type": "proxy_log",
            "data": WireLogRecord.from_buffer(line).model_dump(),
        })


    async def start(self) -> None:
        self.app.state.manager_id = self.config.manager_id
        log_level = (
            "debug" if os.environ.get("LLAMA_DEBUG")
            else "info" if os.environ.get("LLAMA_VERBOSE")
            else "warning"
        )
        config = uvicorn.Config(
            self.app,
            host=self._host,
            port=self._port,
            log_level=log_level,
        )

        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        self._started_at = time.time()

        logger.info("Proxy server started on %s:%s", self._host, self._port)
        self.log(f"Proxy started on {self._host}:{self._port}")


    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        
        if self._task is not None:
            await self._task
        
        self._server = None
        self._task = None
        self._started_at = None

        self.log("Proxy stopped")


    async def restart(self) -> None:
        await self.stop()
        await self.start()


    def status(self) -> dict[str, Any]:
        uptime = None
        if self._started_at is not None:
            uptime = time.time() - self._started_at

        is_running = self._server is not None
        return {
            "state": "running" if is_running else "stopped",
            "host": self._host if is_running else None,
            "port": self._port if is_running else None,
            "uptime": uptime,
            "pid": os.getpid() if is_running else None,
        }


    async def _request_id_middleware(self, request: Request, call_next) -> Response:
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id

        raw_body = await request.body()
        request.state.raw_body = raw_body

        parsed_body = None
        model_id = None
        if raw_body:
            try:
                parsed_body = json.loads(raw_body)
                model_id = parsed_body.get("model") if isinstance(parsed_body, dict) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        headers = dict(request.headers)

        self.request_log.create(request_id, headers, body=parsed_body, model_id=model_id)

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @staticmethod
    def _truncate_body(body, max_len: int = 500):
        if body is None:
            return None
        if isinstance(body, str):
            return body[:max_len] + ("..." if len(body) > max_len else "")
        if isinstance(body, dict):
            s = str(body)
            if len(s) > max_len:
                return s[:max_len] + "..."
        return body