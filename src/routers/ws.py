from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..proxy import proxy_log_buffer, proxy_subscribe, proxy_unsubscribe

router = APIRouter()


@router.websocket("/ws/logs")
async def logs_ws(ws: WebSocket, source: str = Query(default="model-0")):
    await ws.accept()

    if source == "proxy":
        log_buffer = proxy_log_buffer
        subscribe = proxy_subscribe
        unsubscribe = proxy_unsubscribe
    else:
        # Parse model index from source like "model-0", "model-1", etc.
        model_index = 0
        if source.startswith("model-"):
            try:
                model_index = int(source.split("-", 1)[1])
            except (ValueError, IndexError):
                pass
        pms = ws.app.state.process_managers
        if model_index < 0 or model_index >= len(pms):
            await ws.close(code=1008, reason="Invalid model index")
            return
        process_manager = pms[model_index]
        log_buffer = process_manager.log_buffer
        subscribe = process_manager.subscribe
        unsubscribe = process_manager.unsubscribe

    # Send buffered lines
    for line in log_buffer.snapshot():
        await ws.send_json({"type": "log", "id": line.id, "text": line.text})

    # Subscribe for live lines
    q = subscribe()
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
        unsubscribe(q)
