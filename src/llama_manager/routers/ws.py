from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..config import load_config
from ..remote_manager_client import RemoteModelProxy
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
        if process_manager is None:
            await ws.close(code=1008, reason="No logs for remote model")
            return
        log_buffer = process_manager.log_buffer
        subscribe = process_manager.subscribe
        unsubscribe = process_manager.unsubscribe

    # Send buffered lines
    for line in log_buffer.snapshot():
        msg: dict = {"type": "log", "id": line.id, "text": line.text}
        if line.request_id is not None:
            msg["request_id"] = line.request_id
        await ws.send_json(msg)

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
            if not msg:
                break
            await ws.send_json(msg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        unsubscribe(q)


@router.websocket("/ws/manager")
async def manager_ws(ws: WebSocket, token: str = Query(default="")):
    cfg = load_config()
    if not cfg.manager_uplink.enabled:
        await ws.close(code=4403, reason="Uplink disabled")
        return
    if not cfg.manager_uplink.token or token != cfg.manager_uplink.token:
        await ws.close(code=4401, reason="Invalid token")
        return

    await ws.accept()
    ws.app.state.uplink_client_count += 1

    pms = ws.app.state.process_managers
    # Only serve local models (indices within cfg.models range, not None/remote)
    local_pms = [
        (i, pms[i])
        for i in range(len(cfg.models))
        if i < len(pms) and pms[i] is not None and not isinstance(pms[i], RemoteModelProxy)
    ]

    # Send snapshot
    await ws.send_json(
        {
            "type": "snapshot",
            "proxy_port": cfg.api_server.port,
            "models": [
                {
                    "index": i,
                    "name": cfg.models[i].name,
                    "model_id": cfg.models[i].effective_id,
                    "state": pm.get_status()["state"],
                }
                for i, pm in local_pms
            ],
        }
    )

    # Send log history for each local model
    for i, pm in local_pms:
        lines = pm.log_buffer.snapshot()
        if lines:
            await ws.send_json(
                {
                    "type": "log_history",
                    "model": i,
                    "lines": [{"id": ln.id, "text": ln.text} for ln in lines],
                }
            )

    # Subscribe to all local process managers
    queues: dict[int, asyncio.Queue] = {}
    for i, pm in local_pms:
        queues[i] = pm.subscribe()

    # Periodic slots/health push task
    async def push_slots_health():
        import httpx

        while True:
            await asyncio.sleep(3)
            current_cfg = load_config()
            current_pms = ws.app.state.process_managers
            for i, pm in [
                (i, current_pms[i])
                for i in range(len(current_cfg.models))
                if i < len(current_pms)
                and current_pms[i] is not None
                and not isinstance(current_pms[i], RemoteModelProxy)
            ]:
                status = pm.get_status()
                if status["state"] == "running":
                    port = current_cfg.api_server.llama_server_starting_port + i
                    base = f"http://127.0.0.1:{port}"
                    try:
                        async with httpx.AsyncClient(timeout=3) as client:
                            slots_resp = await client.get(f"{base}/slots")
                            if slots_resp.status_code == 200:
                                await ws.send_json(
                                    {
                                        "type": "slots",
                                        "model": i,
                                        "slots": slots_resp.json(),
                                    }
                                )
                    except Exception:
                        pass
                    try:
                        async with httpx.AsyncClient(timeout=3) as client:
                            health_resp = await client.get(f"{base}/health")
                            if health_resp.status_code in (200, 503):
                                await ws.send_json(
                                    {
                                        "type": "health",
                                        "model": i,
                                        "health": health_resp.json(),
                                    }
                                )
                    except Exception:
                        pass

    slots_task = asyncio.create_task(push_slots_health())

    # Fan-out messages from all subscribed queues + handle commands
    async def drain_queues():
        while True:
            tasks = {
                asyncio.ensure_future(q.get()): i for i, q in queues.items()
            }
            recv_task = asyncio.ensure_future(ws.receive_text())
            all_tasks = list(tasks.keys()) + [recv_task]

            done, pending = await asyncio.wait(
                all_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

            if recv_task in done:
                # Handle incoming command
                try:
                    data = recv_task.result()
                    cmd = json.loads(data)
                    await _handle_command(cmd, ws.app.state.process_managers)
                except Exception:
                    pass
                # Re-cancel queue tasks explicitly
                for t in tasks:
                    if t not in done:
                        t.cancel()
                continue

            for t in done:
                if t in tasks:
                    model_idx = tasks[t]
                    msg = t.result()
                    if not msg:
                        # Subscriber shut down — remove it
                        queues.pop(model_idx, None)
                        continue
                    msg_with_model = {**msg, "model": model_idx}
                    await ws.send_json(msg_with_model)

    try:
        await drain_queues()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        ws.app.state.uplink_client_count -= 1
        slots_task.cancel()
        for i, pm in local_pms:
            if i in queues:
                pm.unsubscribe(queues[i])


async def _handle_command(cmd: dict, pms: list) -> None:
    t = cmd.get("type")
    model_idx = cmd.get("model", 0)
    if model_idx < 0 or model_idx >= len(pms):
        return
    pm = pms[model_idx]
    if pm is None or isinstance(pm, RemoteModelProxy):
        return
    if t == "start":
        asyncio.create_task(pm.start())
    elif t == "stop":
        asyncio.create_task(pm.stop())
    elif t == "restart":
        asyncio.create_task(pm.restart())
