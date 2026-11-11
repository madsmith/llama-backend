from __future__ import annotations

import os

from fastapi import Query
from fastapi.responses import JSONResponse

from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.proxy import ActiveRequestManager
from llama_manager.util.system import total_ram_bytes


class StatusRoutes:
    def __init__(self, manager: LlamaManager) -> None:
        self.manager = manager

    async def _memory(self) -> dict | None:
        """What autoload's live budget sees: free memory in the pool models load into."""
        snap = await self.manager.measure_memory()
        if snap is None:
            return None
        return {"available_mib": snap.available // 1024**2, "total_mib": snap.total // 1024**2, "source": snap.source}

    async def get_system(self):
        total = total_ram_bytes()
        return {
            "total_ram_mib": total // (1024 * 1024) if total else None,
            "cwd": os.getcwd(),
            "memory": await self._memory(),
        }

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
        cancellable = set(ActiveRequestManager.list_cancellable(model_suid))
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

    async def cancel_slot(self, model_suid: str = Query(...), slot: int = Query(...)):
        if ActiveRequestManager.cancel(model_suid, slot):
            return {"status": "cancelled"}
        return JSONResponse(
            {"error": "no active cancellable request on this slot"}, status_code=404
        )
