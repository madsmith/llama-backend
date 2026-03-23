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

from ..config import load_config
from .lifecycle import _ttl_checker, get_ttl_task, set_ttl_task
from .openai import openai_proxy
from .request_log import request_log
from .subscription import proxy_log

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app + route registration
# ---------------------------------------------------------------------------

proxy_app = FastAPI(title="Llama Proxy")

proxy_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@proxy_app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
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
    request_log.create(request_id, headers, body=parsed_body, model_id=model_id)

    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


proxy_app.api_route(
    "/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)(openai_proxy)

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

_server: uvicorn.Server | None = None
_task: asyncio.Task | None = None
_proxy_host: str | None = None
_proxy_port: int | None = None
_proxy_started_at: float | None = None


async def start_proxy() -> None:
    global _server, _task, _proxy_host, _proxy_port, _proxy_started_at
    cfg = load_config()
    api = cfg.api_server
    config = uvicorn.Config(
        proxy_app,
        host=api.host,
        port=api.port,
        log_level="debug"
        if os.environ.get("LLAMA_DEBUG")
        else "info"
        if os.environ.get("LLAMA_VERBOSE")
        else "warning",
    )
    _server = uvicorn.Server(config)
    _task = asyncio.create_task(_server.serve())
    _proxy_host = api.host
    _proxy_port = api.port
    _proxy_started_at = time.time()
    logger.info("Proxy server started on %s:%s", api.host, api.port)
    proxy_log(f"Proxy started on {api.host}:{api.port}")
    print(f"[proxy] started on {api.host}:{api.port}")
    set_ttl_task(asyncio.create_task(_ttl_checker()))


async def stop_proxy() -> None:
    global _server, _task, _proxy_host, _proxy_port, _proxy_started_at
    ttl_task = get_ttl_task()
    if ttl_task is not None:
        ttl_task.cancel()
        set_ttl_task(None)
    if _server is not None:
        _server.should_exit = True
    if _task is not None:
        await _task
    _server = None
    _task = None
    _proxy_host = None
    _proxy_port = None
    _proxy_started_at = None
    proxy_log("Proxy stopped")
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
