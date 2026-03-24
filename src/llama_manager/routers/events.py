from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from llama_manager.event_bus import bus

router = APIRouter()


async def _pump(ws: WebSocket, q: asyncio.Queue[dict]) -> None:
    """Send queued events to the WebSocket; silently stops on send failure."""
    try:
        while True:
            event = await q.get()
            await ws.send_json(event)
    except Exception:
        pass


@router.websocket("/ws/events")
async def events_ws(ws: WebSocket):
    await ws.accept()
    q = bus.subscribe()
    send_task = asyncio.create_task(_pump(ws, q))
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        send_task.cancel()
        bus.unsubscribe(q)
