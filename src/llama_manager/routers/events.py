from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from llama_manager.util.event_bus import EventBus

class EventRouter:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
    
    async def events_ws(self, ws: WebSocket):
        await ws.accept()
        q = self.event_bus.subscribe()
        send_task = asyncio.create_task(self._pump(ws, q))
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            send_task.cancel()
            self.event_bus.unsubscribe(q)
    
    async def _pump(self, ws: WebSocket, q: asyncio.Queue[dict]) -> None:
        """Send queued events to the WebSocket; silently stops on send failure."""
        try:
            while True:
                event = await q.get()
                await ws.send_json(event)
        except Exception:
            pass


def make_router(event_bus: EventBus) -> APIRouter:
    router = APIRouter()

    event_router = EventRouter(event_bus)

    router.websocket("/ws/events")(event_router.events_ws)

    return router
