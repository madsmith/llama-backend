from __future__ import annotations

from fastapi import APIRouter

from .routes import health, props, slots

router = APIRouter(prefix="/api/status", tags=["status"])

router.get("/health")(health.get_health)
router.get("/slots")(slots.get_slots)
router.get("/props")(props.get_props)
