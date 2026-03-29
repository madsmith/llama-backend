from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from llama_manager.config import AppConfig
from llama_manager.manager.backends import LocalManagedModel

from .subscription import proxy_log

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager

# ---------------------------------------------------------------------------
# JIT model server start + TTL tracking
# ---------------------------------------------------------------------------

_llama_manager: LlamaManager | None = None
_model_last_activity: dict[int, float] = {}
_ttl_task: asyncio.Task | None = None


def set_llama_manager(manager: LlamaManager) -> None:
    global _llama_manager
    _llama_manager = manager


def get_llama_manager() -> LlamaManager:
    assert _llama_manager is not None
    return _llama_manager


def touch_model(model_index: int) -> None:
    """Record activity for a model, resetting its TTL timer."""
    _model_last_activity[model_index] = time.monotonic()


async def task_ttl_checker(config: AppConfig) -> None:
    """Background task that stops idle models whose TTL has expired."""
    while True:
        await asyncio.sleep(30)
        try:
            local_models = get_llama_manager().get_local_models()
            now = time.monotonic()
            for m in config.models:
                if m.model_ttl is None or m.type == "remote":
                    continue
                local_model = local_models.get(m.suid)
                if local_model is None or local_model.state.value != "running":
                    continue
                last = _model_last_activity.get(m.suid)
                if last is None:
                    continue
                if now - last > m.model_ttl * 60:
                    name = m.name or m.suid
                    proxy_log(f"TTL expired for [{name}], stopping server")
                    await local_model.stop()
        except Exception:
            pass  # don't crash the background task


def get_ttl_task() -> asyncio.Task | None:
    return _ttl_task


def set_ttl_task(task: asyncio.Task | None) -> None:
    global _ttl_task
    _ttl_task = task


async def ensure_model_server(model_index: int, config: AppConfig) -> None:
    """Start model server on-demand if JIT or TTL is enabled and server isn't running."""
    model = config.models[model_index] if model_index < len(config.models) else None
    has_ttl = model is not None and model.model_ttl is not None
    if not config.api_server.jit_model_server and not has_ttl:
        return
    cfg = get_llama_manager().config
    suid = cfg.models[model_index].suid if model_index < len(cfg.models) else None
    local_model = get_llama_manager().get_local_models().get(suid) if suid else None
    if local_model is None:
        return  # remote model or out of range — no local process to start
    if local_model.state.value == "running":
        return
    if local_model.state.value not in ("stopped", "error"):
        return

    timeout = config.api_server.jit_timeout or 80
    proxy_log(f"JIT: model server [{model_index}] is {local_model.state.value}, starting...")
    await local_model.start()

    elapsed = 0.0
    while elapsed < timeout:
        state = local_model.state.value
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
