from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from ... import llama_client


async def get_slots(request: Request, model: int = Query(default=0)):
    pms = getattr(request.app.state, "process_managers", [])
    pm = pms[model] if model < len(pms) else None
    data = await llama_client.get_slots(model)
    if data is None:
        return JSONResponse({"error": "unavailable"}, status_code=503)
    if pm is not None:
        progress = pm.get_prompt_progress()
        if progress:
            for slot in data:
                info = progress.get(slot.get("id"))
                if info:
                    slot["prompt_progress"] = info["progress"]
                    slot["prompt_n_processed"] = info["n_processed"]
                    slot["prompt_n_total"] = info["n_total"]
    return data
