from __future__ import annotations

from fastapi import APIRouter

from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.proxy import ProxyServer

from .routes.proxy import ProxyRoutes
from .routes.server import ServerRoutes

def make_router(manager: LlamaManager) -> APIRouter:
    router = APIRouter(prefix="/api/server", tags=["server"])

    server_routes = ServerRoutes(manager=manager)
    router.get("/status")(server_routes.get_status)
    router.post("/start")(server_routes.start)
    router.post("/stop")(server_routes.stop)
    router.post("/restart")(server_routes.restart)

    proxy_routes = ProxyRoutes(manager.proxy)
    router.get("/proxy-status")(proxy_routes.status)
    router.post("/proxy-start")(proxy_routes.start)
    router.post("/proxy-stop")(proxy_routes.stop)
    router.post("/proxy-restart")(proxy_routes.restart)

    return router
