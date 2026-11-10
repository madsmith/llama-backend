from __future__ import annotations

from fastapi import APIRouter

from .routes.fs import FsRoutes


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/fs", tags=["fs"])
    routes = FsRoutes()
    router.get("/browse")(routes.browse)
    router.get("/gguf")(routes.gguf_info)
    return router
