from __future__ import annotations

from fastapi import APIRouter

from .routes import health, props, requests, slots

router = APIRouter(prefix="/api/status", tags=["status"])

router.get("/health")(health.get_health)
router.get("/slots")(slots.get_slots)
router.post("/slots/cancel")(slots.cancel_slot)
router.get("/props")(props.get_props)
router.get("/requests")(requests.list_requests)
router.get("/requests/{request_id}")(requests.get_request)
