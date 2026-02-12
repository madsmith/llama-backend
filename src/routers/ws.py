from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/logs")
async def logs_ws(ws: WebSocket):
    await ws.accept()
    process_manager = ws.app.state.process_manager

    # Send buffered lines
    for line in process_manager.log_buffer.snapshot():
        await ws.send_json({"type": "log", "id": line.id, "text": line.text})

    # Subscribe for live lines
    q = process_manager.subscribe()
    try:
        while True:
            # Race: next log message vs client disconnect
            q_task = asyncio.ensure_future(q.get())
            recv_task = asyncio.ensure_future(ws.receive())
            done, pending = await asyncio.wait(
                [q_task, recv_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if recv_task in done:
                # Client disconnected (or sent a close frame)
                break

            msg = q_task.result()
            if msg is None:
                break
            await ws.send_json(msg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        process_manager.unsubscribe(q)
