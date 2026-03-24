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

from llama_manager.config import AppConfig

from .lifecycle import task_ttl_checker, get_ttl_task, set_ttl_task
from .openai import OpenAIProxy
from .request_log import RequestLog
from .subscription import proxy_log

logger = logging.getLogger(__name__)


class ProxyServer:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

        self.app = FastAPI(title="Llama Proxy")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.app.middleware("http")(self._request_id_middleware)
        self._openai = OpenAIProxy(config)
        self.app.api_route(
            "/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )(self._openai)

        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._host: str = config.api_server.host
        self._port: int = config.api_server.port
        self._started_at: float | None = None


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
        proxy_log(f"Proxy started on {self._host}:{self._port}")
        set_ttl_task(asyncio.create_task(task_ttl_checker(config)))


    async def stop(self) -> None:
        ttl_task = get_ttl_task()
        if ttl_task is not None:
            ttl_task.cancel()
            set_ttl_task(None)

        if self._server is not None:
            self._server.should_exit = True
        
        if self._task is not None:
            await self._task
        
        self._server = None
        self._task = None
        self._started_at = None

        proxy_log("Proxy stopped")


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

        RequestLog.get_instance().create(request_id, headers, body=parsed_body, model_id=model_id)

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response