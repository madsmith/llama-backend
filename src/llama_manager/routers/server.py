from __future__ import annotations

from fastapi import APIRouter

from llama_manager.proxy import ProxyServer
from llama_manager.event_bus import EventBus

from .routes import server
from .routes.proxy import ProxyRoutes
from .routes.config import ConfigRoutes

def make_router(proxy: ProxyServer, event_bus: EventBus) -> APIRouter:
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

    config_routes = ConfigRoutes(event_bus)
    router.get("/config")(config_routes.get_config)
    router.put("/config")(config_routes.put_config)
    router.post("/config/generate-token")(config_routes.generate_token)

    return router
