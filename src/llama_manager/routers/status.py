from __future__ import annotations

from fastapi import APIRouter

from llama_manager.manager.llama_manager import LlamaManager

from .routes import requests
from .routes.status import StatusRoutes


def make_router(manager: LlamaManager) -> APIRouter:
    router = APIRouter(prefix="/api/status", tags=["status"])
    status_routes = StatusRoutes(manager)

    router.get("/health")(status_routes.get_health)
    router.get("/slots")(status_routes.get_slots)
    router.post("/slots/cancel")(status_routes.cancel_slot)
    router.get("/props")(status_routes.get_props)
    router.get("/requests")(requests.list_requests)
    router.get("/requests/{request_id}")(requests.get_request)

    return router
