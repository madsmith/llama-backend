from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import ServerConfig, load_config, save_config

router = APIRouter(prefix="/api/server", tags=["server"])


def _process_manager(request: Request):
    return request.app.state.process_manager


def _status_response(process_manager):
    s = process_manager.get_status()
    if s["state"] == "error":
        lines = process_manager.log_buffer.snapshot()
        s["error"] = lines[-1].text if lines else "Unknown error"
        return JSONResponse(s, status_code=500)
    return s


@router.get("/status")
async def status(request: Request):
    return _process_manager(request).get_status()


@router.post("/start")
async def start(request: Request):
    process_manager = _process_manager(request)
    await process_manager.start()
    return _status_response(process_manager)


@router.post("/stop")
async def stop(request: Request):
    process_manager = _process_manager(request)
    await process_manager.stop()
    return _status_response(process_manager)


@router.post("/restart")
async def restart(request: Request):
    process_manager = _process_manager(request)
    await process_manager.restart()
    return _status_response(process_manager)


@router.get("/config")
async def get_config():
    return load_config().model_dump()


@router.put("/config")
async def put_config(cfg: ServerConfig):
    save_config(cfg)
    return cfg.model_dump()
