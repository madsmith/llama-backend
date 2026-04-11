from __future__ import annotations

from fastapi import Query
from fastapi.responses import JSONResponse

from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.manager.backends import LocalManagedModel, RemoteModelProxy



class ServerRoutes:
    def __init__(self, manager: LlamaManager):
        self.manager: LlamaManager = manager

    async def start(self, suid: str = Query(...)):
        return await self._send_command(suid, "start")

    async def stop(self, suid: str = Query(...)):
        return await self._send_command(suid, "stop")

    async def restart(self, suid: str = Query(...)):
        return await self._send_command(suid, "restart")

    async def get_status(self, suid: str = Query(...)):
        model = self._find(suid)
        if model is None:
            return JSONResponse({"error": "Server not found"}, status_code=404)
        if isinstance(model, RemoteModelProxy):
            return model.get_status()
        return self._status_response(model)

    async def _send_command(self, suid: str, command: str):
        model = self._find(suid)
        if model is None:
            return JSONResponse({"error": "Server not found"}, status_code=404)
        if isinstance(model, RemoteModelProxy):
            await model.send_command(command)
            return model.get_status()
        command_fn = getattr(model, command, None)
        if command_fn is None:
            return JSONResponse({"error": "Command not found"}, status_code=404)
        await command_fn()
        return self._status_response(model)

    def _find(self, suid: str) -> LocalManagedModel | RemoteModelProxy | None:
        local_model = self.manager.get_local_models().get(suid)
        if local_model is not None:
            return local_model
        for remote_model in self.manager.get_remote_models():
            if remote_model.get_suid() == suid:
                return remote_model
        return None

    @staticmethod
    def _status_response(local_model: LocalManagedModel):
        status = local_model.get_status()
        if status["state"] == "error":
            lines = local_model.log_buffer.snapshot()
            status["error"] = str(lines[-1]) if lines else "Unknown error"
            return JSONResponse(status, status_code=500)
        return status
