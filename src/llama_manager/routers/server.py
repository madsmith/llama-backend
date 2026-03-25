from __future__ import annotations

from fastapi import APIRouter

from llama_manager.proxy import ProxyServer

from .routes import server
from .routes.proxy import ProxyRoutes

def make_router(proxy: ProxyServer) -> APIRouter:
    router = APIRouter(prefix="/api/server", tags=["server"])

    router.get("/status")(server.get_status)
    router.post("/start")(server.start)
    router.post("/stop")(server.stop)
    router.post("/restart")(server.restart)

    proxy_routes = ProxyRoutes(proxy)
    router.get("/proxy-status")(proxy_routes.status)
    router.post("/proxy-start")(proxy_routes.start)
    router.post("/proxy-stop")(proxy_routes.stop)
    router.post("/proxy-restart")(proxy_routes.restart)

    return router
