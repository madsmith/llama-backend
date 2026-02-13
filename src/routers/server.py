from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ..config import AppConfig, load_config, save_config
from ..process_manager import ProcessManager
from ..proxy import get_proxy_status, start_proxy, stop_proxy, restart_proxy

router = APIRouter(prefix="/api/server", tags=["server"])


def _process_manager(request: Request, model: int = 0) -> ProcessManager | None:
    pms = request.app.state.process_managers
    if model < 0 or model >= len(pms):
        raise IndexError(f"model index {model} out of range")
    return pms[model]


def _remote_status() -> dict:
    return {"state": "remote", "pid": None, "host": None, "port": None, "uptime": None}


def _status_response(process_manager):
    s = process_manager.get_status()
    if s["state"] == "error":
        lines = process_manager.log_buffer.snapshot()
        s["error"] = lines[-1].text if lines else "Unknown error"
        return JSONResponse(s, status_code=500)
    return s


@router.get("/status")
async def status(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return _remote_status()
    return pm.get_status()


@router.post("/start")
async def start(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return JSONResponse({"error": "Cannot start a remote model"}, status_code=400)
    await pm.start()
    return _status_response(pm)


@router.post("/stop")
async def stop(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return JSONResponse({"error": "Cannot stop a remote model"}, status_code=400)
    await pm.stop()
    return _status_response(pm)


@router.post("/restart")
async def restart(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return JSONResponse({"error": "Cannot restart a remote model"}, status_code=400)
    await pm.restart()
    return _status_response(pm)


@router.get("/proxy-status")
async def proxy_status():
    return get_proxy_status()


@router.post("/proxy-start")
async def proxy_start():
    await start_proxy()
    return get_proxy_status()


@router.post("/proxy-stop")
async def proxy_stop():
    await stop_proxy()
    return get_proxy_status()


@router.post("/proxy-restart")
async def proxy_restart():
    await restart_proxy()
    return get_proxy_status()


@router.get("/config")
async def get_config():
    return load_config().model_dump(by_alias=True)


@router.put("/config")
async def put_config(cfg: AppConfig, request: Request):
    save_config(cfg)
    # Sync process managers list to match new model count and types
    pms: list[ProcessManager | None] = request.app.state.process_managers
    while len(pms) < len(cfg.models):
        idx = len(pms)
        m = cfg.models[idx]
        pms.append(None if m.type == "remote" else ProcessManager(idx))
    # Update type for existing indices (local<->remote switch)
    for i, m in enumerate(cfg.models):
        if i >= len(pms):
            break
        if m.type == "remote" and pms[i] is not None:
            pm = pms[i]
            if pm is not None and pm.state.value == "stopped":
                pms[i] = None
        elif m.type != "remote" and pms[i] is None:
            pms[i] = ProcessManager(i)
    # Shrink if models were removed (only trim stopped managers from the end)
    while len(pms) > len(cfg.models):
        pm = pms[-1]
        if pm is not None and pm.state.value != "stopped":
            break
        pms.pop()
    from ..proxy import set_process_managers
    set_process_managers(pms)
    return cfg.model_dump(by_alias=True)
