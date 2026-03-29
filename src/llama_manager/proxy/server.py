from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from typing import Any

from typing import TYPE_CHECKING

from llama_manager.config import AppConfig
from llama_manager.log_buffer import LogBuffer

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager
from .handler import ProxyHandler
from .openai import OpenAIAdapter
from .request_log import RequestLog
from .subscription import set_proxy_server

logger = logging.getLogger(__name__)


class ProxyServer:
    def __init__(self, manager: LlamaManager) -> None:
        self._manager = manager
        self.config: AppConfig = manager.config
        self.log_buffer = LogBuffer(maxlen=self.config.web_ui.log_buffer_size)
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

        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._host: str = self.config.api_server.host
        self._port: int = self.config.api_server.port
        self._started_at: float | None = None

        set_proxy_server(self)

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

    def log(self, text: str, *, request_id: str | None = None) -> None:
        stamped = f"[{time.strftime('%H:%M:%S')}] {text}"
        line = self.log_buffer.append(stamped, request_id=request_id)
        self._manager.event_bus.publish({
            "type": "proxy_log",
            "data": {"line_id": line.id, "text": line.text, "request_id": request_id},
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