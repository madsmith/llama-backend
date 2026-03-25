from __future__ import annotations

import logging
import os
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

from .config import load_config
from .dev import DevViteService
from .llama_manager import LlamaManager
from .routers import status, ws
from .routers.events import router as events_router
from .routers.remotes import router as remotes_router
from .routers.server import make_router as make_server_router
from .routers.ws_v2 import make_router as make_ws_v2_router

ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"

DEV_MODE = os.environ.get("LLAMA_DEV", "").lower() in ("1", "true", "yes")

config = load_config()
manager = LlamaManager(config)
vite = DevViteService() if DEV_MODE else None

app = FastAPI(title="Llama Server Manager", lifespan=manager.get_lifespan(vite=vite))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(make_server_router(manager.proxy))
app.include_router(make_ws_v2_router(manager))
app.include_router(status.router)
app.include_router(ws.router)
app.include_router(events_router)
app.include_router(remotes_router)

# In prod mode, serve the built frontend as static files
if not DEV_MODE and DIST_DIR.is_dir():
    from starlette.responses import FileResponse

    @app.get("/{path:path}")
    async def spa_catch_all(path: str):
        file = DIST_DIR / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(DIST_DIR / "index.html")
