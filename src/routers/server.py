from __future__ import annotations

from fastapi import APIRouter

from .routes import config, proxy, server

router = APIRouter(prefix="/api/server", tags=["server"])

router.get("/status")(server.get_status)
router.post("/start")(server.start)
router.post("/stop")(server.stop)
router.post("/restart")(server.restart)

router.get("/proxy-status")(proxy.proxy_status)
router.post("/proxy-start")(proxy.proxy_start)
router.post("/proxy-stop")(proxy.proxy_stop)
router.post("/proxy-restart")(proxy.proxy_restart)

router.get("/config")(config.get_config)
router.put("/config")(config.put_config)
