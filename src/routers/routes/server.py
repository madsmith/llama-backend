from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from ...process_manager import ProcessManager


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


async def get_status(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return _remote_status()
    return pm.get_status()


async def start(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return JSONResponse({"error": "Cannot start a remote model"}, status_code=400)
    await pm.start()
    return _status_response(pm)


async def stop(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return JSONResponse({"error": "Cannot stop a remote model"}, status_code=400)
    await pm.stop()
    return _status_response(pm)


async def restart(request: Request, model: int = Query(default=0)):
    pm = _process_manager(request, model)
    if pm is None:
        return JSONResponse({"error": "Cannot restart a remote model"}, status_code=400)
    await pm.restart()
    return _status_response(pm)
