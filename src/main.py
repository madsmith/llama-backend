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


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import load_config
from .process_manager import ProcessManager
from .proxy import start_proxy, stop_proxy, shutdown_proxy_subscribers, set_process_managers
from .routers import server, status, ws

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

DEV_MODE = os.environ.get("LLAMA_DEV", "").lower() in ("1", "true", "yes")


async def _start_vite() -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        "pnpm", "dev",
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.process_managers = [ProcessManager(i) for i in range(len(cfg.models))]
    set_process_managers(app.state.process_managers)
    vite_proc = None
    if DEV_MODE:
        vite_proc = await _start_vite()
        print(f"[dev] Vite dev server started (pid {vite_proc.pid})")
    await start_proxy()
    yield
    await stop_proxy()
    shutdown_proxy_subscribers()
    for pm in app.state.process_managers:
        pm.shutdown_subscribers()
        await pm.stop()
    if vite_proc:
        await _stop_vite(vite_proc)
        print("[dev] Vite dev server stopped")


app = FastAPI(title="Llama Server Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(server.router)
app.include_router(status.router)
app.include_router(ws.router)

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
