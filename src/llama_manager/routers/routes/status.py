from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse

from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.proxy import ActiveRequestManager


class StatusRoutes:
    def __init__(self, manager: LlamaManager) -> None:
        self.manager = manager

    def _model_index(self, model_suid: str) -> int | None:
        return next(
            (i for i, m in enumerate(self.manager.config.models) if m.suid == model_suid),
            None,
        )

    async def get_health(self, model_suid: str = Query(...)):
        client = self.manager.get_client(model_suid)
        if client is None:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        data = await client.get_health()
        if data is None:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return data

    async def get_slots(self, model_suid: str = Query(...)):
        local_model = self.manager.get_local_models().get(model_suid)
        client = self.manager.get_client(model_suid)
        if client is None:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        data = await client.get_slots()
        if data is None:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        model_index = self._model_index(model_suid)
        cancellable = set(ActiveRequestManager.list_cancellable(model_index)) if model_index is not None else set()
        if local_model is not None:
            progress = local_model.get_prompt_progress()
            if progress:
                for slot in data:
                    info = progress.get(slot.get("id"))
                    if info:
                        slot["prompt_progress"] = info["progress"]
                        slot["prompt_n_processed"] = info["n_processed"]
                        slot["prompt_n_total"] = info["n_total"]
        for slot in data:
            slot["cancellable"] = slot.get("id") in cancellable
        return data

    async def get_props(self, model_suid: str = Query(...)):
        client = self.manager.get_client(model_suid)
        if client is None:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        data = await client.get_props()
        if data is None:
            return JSONResponse({"error": "unavailable"}, status_code=503)
        return data

    async def cancel_slot(self, model_suid: str = Query(...), slot: int = Query(...)):
        model_index = self._model_index(model_suid)
        if model_index is None:
            return JSONResponse({"error": "model not found"}, status_code=404)
        if ActiveRequestManager.cancel(model_index, slot):
            return {"status": "cancelled"}
        return JSONResponse(
            {"error": "no active cancellable request on this slot"}, status_code=404
        )
