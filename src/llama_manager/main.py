from __future__ import annotations

import asyncio
import logging
import os
import signal
from contextlib import asynccontextmanager
from pathlib import Path

if os.environ.get("LLAMA_DEBUG", "").lower() in ("1", "true", "yes"):
    logging.basicConfig(level=logging.DEBUG)
elif os.environ.get("LLAMA_VERBOSE", "").lower() in ("1", "true", "yes"):
    logging.basicConfig(level=logging.INFO)
elif not logging.root.handlers:
    logging.basicConfig(level=logging.WARNING)

logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import AppConfig, load_config
from .process_manager import ProcessManager
from .proxy import (
    ProxyServer,
    set_process_managers,
    shutdown_proxy_subscribers,
)
from .event_bus import bus as event_bus
from .remote_manager_client import RemoteManagerClient, RemoteModelProxy
from .routers import status, ws
from .routers.events import router as events_router
from .routers.remotes import router as remotes_router
from .routers.server import make_router as make_server_router
from .routers.ws_v2 import make_router as make_ws_v2_router

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

DEV_MODE = os.environ.get("LLAMA_DEV", "").lower() in ("1", "true", "yes")


async def _start_vite() -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        "pnpm",
        "dev",
        cwd=str(FRONTEND_DIR),
        stdout=None,
        stderr=None,
    )
    return proc


async def _stop_vite(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


async def _data_publisher(app: FastAPI) -> None:
    """Publish slot and health events for running local models to the event bus."""
    import httpx

    last_slots: dict[str, list[dict]] = {}

    while True:
        try:
            pms = app.state.process_managers
            for pm in pms:
                if pm is None or isinstance(pm, RemoteModelProxy):
                    continue
                if pm.state.value != "running":
                    continue

                if isinstance(pm, ProcessManager):
                    base = pm.get_server_address()
                    try:
                        async with httpx.AsyncClient(timeout=2) as client:
                            resp = await client.get(f"{base}/slots")
                            if resp.status_code == 200:
                                slots = resp.json()
                                last_slots[pm.get_server_identifier()] = slots
                                event_bus.publish({"type": "slots", "server_id": pm.get_server_identifier(), "slots": slots})
                    except Exception:
                        pass
                    try:
                        async with httpx.AsyncClient(timeout=2) as client:
                            resp = await client.get(f"{base}/health")
                            if resp.status_code in (200, 503):
                                event_bus.publish({"type": "health", "server_id": pm.get_server_identifier(), "health": resp.json()})
                    except Exception:
                        pass
                else:
                    logger.warning("Unknown process manager type: %s", type(pm))
        except Exception:
            pass

        active = any(
            s.get("is_processing")
            for slots in last_slots.values()
            for s in slots
        )

        await asyncio.sleep(0.5 if active else 3.0)


def _make_process_managers(config: AppConfig) -> list[ProcessManager | None]:
    return [
        None if model.type == "remote" else ProcessManager(idx, config)
        for idx, model in enumerate(config.models)
    ]


class LlamaManagerLifecycle:
    def __init__(self, config: AppConfig, proxy: ProxyServer) -> None:
        self.config = config
        self.proxy = proxy

    def get_lifespan(self):
        proxy = self.proxy

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            app.state.process_managers = _make_process_managers(self.config)
            app.state.remote_manager_clients = []
            app.state.uplink_client_count = 0
            set_process_managers(app.state.process_managers)
            vite_proc = None
            if DEV_MODE:
                vite_proc = await _start_vite()
                print(f"[dev] Vite dev server started (pid {vite_proc.pid})")

            await proxy.start()

            # Auto-start models that have auto-start enabled
            for i, m in enumerate(self.config.models):
                pm = app.state.process_managers[i]
                if m.auto_start and pm is not None:
                    print(f"[auto-start] Starting model {m.name or i} ...")
                    await pm.start()

            # Start remote manager clients
            for i, remote_config in enumerate(self.config.remote_managers):
                if remote_config.enabled and remote_config.host:
                    client = RemoteManagerClient(i, remote_config, self.config, app)
                    app.state.remote_manager_clients.append(client)
                    await client.start()

            data_publisher_task = asyncio.create_task(_data_publisher(app))

            yield

            # === Teardown ===
            data_publisher_task.cancel()

            # Stop remote manager clients
            for client in app.state.remote_manager_clients:
                await client.stop()

            await proxy.stop()
            shutdown_proxy_subscribers()

            for pm in app.state.process_managers:
                if pm is not None:
                    pm.shutdown_subscribers()
                    await pm.stop()
            if vite_proc:
                await _stop_vite(vite_proc)
                print("[dev] Vite dev server stopped")

        return lifespan


config = load_config()
proxy_server = ProxyServer(config)
lifecycle = LlamaManagerLifecycle(config, proxy_server)

app = FastAPI(title="Llama Server Manager", lifespan=lifecycle.get_lifespan())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(make_server_router(proxy_server))
app.include_router(make_ws_v2_router(proxy_server))
app.include_router(status.router)
app.include_router(ws.router)
app.include_router(events_router)
app.include_router(remotes_router)

# In prod mode, serve the built frontend as static files
if not DEV_MODE and DIST_DIR.is_dir():
    # SPA catch-all: serve index.html for any non-API route
    from starlette.responses import FileResponse

    @app.get("/{path:path}")
    async def spa_catch_all(path: str):
        # If the file exists in dist, serve it; otherwise serve index.html
        file = DIST_DIR / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(DIST_DIR / "index.html")
