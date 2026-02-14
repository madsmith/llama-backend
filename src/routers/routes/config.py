from __future__ import annotations

from fastapi import Request

from ...config import AppConfig, load_config, save_config
from ...process_manager import ProcessManager


async def get_config():
    return load_config().model_dump()


async def put_config(cfg: AppConfig, request: Request):
    save_config(cfg)
    # Sync process managers list to match new model count and types
    pms: list[ProcessManager | None] = request.app.state.process_managers
    while len(pms) < len(cfg.models):
        idx = len(pms)
        m = cfg.models[idx]
        pms.append(None if m.type == "remote" else ProcessManager(idx, cfg))
    # Update type for existing indices (local<->remote switch)
    for i, m in enumerate(cfg.models):
        if i >= len(pms):
            break
        if m.type == "remote" and pms[i] is not None:
            pm = pms[i]
            if pm is not None and pm.state.value == "stopped":
                pms[i] = None
        elif m.type != "remote" and pms[i] is None:
            pms[i] = ProcessManager(i, cfg)
    # Shrink if models were removed (only trim stopped managers from the end)
    while len(pms) > len(cfg.models):
        pm = pms[-1]
        if pm is not None and pm.state.value != "stopped":
            break
        pms.pop()
    from ...proxy import set_process_managers

    set_process_managers(pms)
    return cfg.model_dump()
