from __future__ import annotations

import asyncio
import time

from ..config import load_config
from ..process_manager import ProcessManager
from .subscription import proxy_log

# ---------------------------------------------------------------------------
# JIT model server start + TTL tracking
# ---------------------------------------------------------------------------

_process_managers: list[ProcessManager | None] = []
_model_last_activity: dict[int, float] = {}
_ttl_task: asyncio.Task | None = None


def touch_model(model_index: int) -> None:
    """Record activity for a model, resetting its TTL timer."""
    _model_last_activity[model_index] = time.monotonic()


async def _ttl_checker() -> None:
    """Background task that stops idle models whose TTL has expired."""
    while True:
        await asyncio.sleep(30)
        try:
            cfg = load_config()
            now = time.monotonic()
            for i, m in enumerate(cfg.models):
                if m.model_ttl is None or m.type == "remote":
                    continue
                if i >= len(_process_managers):
                    continue
                pm = _process_managers[i]
                if pm is None or pm.state.value != "running":
                    continue
                last = _model_last_activity.get(i)
                if last is None:
                    continue
                if now - last > m.model_ttl * 60:
                    name = m.name or f"model-{i}"
                    proxy_log(f"TTL expired for [{name}], stopping server")
                    await pm.stop()
        except Exception:
            pass  # don't crash the background task


def set_process_managers(pms: list[ProcessManager | None]) -> None:
    global _process_managers
    _process_managers = pms


def get_ttl_task() -> asyncio.Task | None:
    return _ttl_task


def set_ttl_task(task: asyncio.Task | None) -> None:
    global _ttl_task
    _ttl_task = task


async def ensure_model_server(model_index: int = 0) -> None:
    """Start model server on-demand if JIT or TTL is enabled and server isn't running."""
    cfg = load_config()
    m = cfg.models[model_index] if model_index < len(cfg.models) else None
    has_ttl = m is not None and m.model_ttl is not None
    if not cfg.api_server.jit_model_server and not has_ttl:
        return
    if model_index < 0 or model_index >= len(_process_managers):
        return
    pm = _process_managers[model_index]
    if pm is None:
        return  # remote model — no local process to start
    if pm.state.value == "running":
        return
    if pm.state.value not in ("stopped", "error"):
        return

    timeout = cfg.api_server.jit_timeout or 80
    proxy_log(f"JIT: model server [{model_index}] is {pm.state.value}, starting...")
    await pm.start()

    elapsed = 0.0
    while elapsed < timeout:
        state = pm.state.value
        if state == "running":
            proxy_log(f"JIT: model server [{model_index}] ready ({elapsed:.1f}s)")
            return
        if state == "error":
            raise RuntimeError(f"Model server [{model_index}] failed to start")
        await asyncio.sleep(0.5)
        elapsed += 0.5

    raise RuntimeError(
        f"Model server [{model_index}] did not become ready within {timeout}s"
    )
