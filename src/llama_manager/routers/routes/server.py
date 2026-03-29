from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from llama_manager.manager.llama_manager import LlamaManager
from llama_manager.manager.backends import LocalManagedModel, RemoteModelProxy


class ServerRoutes:
    def __init__(self, manager: LlamaManager):
        self.manager: LlamaManager = manager

    async def start(
        self,
        request: Request,
        server_id: str = Query(...),
        model_suid: int = Query(...),
    ):
        return self._send_command(request, server_id, model_suid, "start")

    async def stop(
        self,
        request: Request,
        server_id: str = Query(...),
        model_suid: int = Query(...),
    ):
        return self._send_command(request, server_id, model_suid, "stop")

    async def restart(
        self,
        request: Request,
        server_id: str = Query(...),
        model_suid: int = Query(...),
    ):
        return self._send_command(request, server_id, model_suid, "restart")

    async def get_status(
        self,
        request: Request,
        server_id: str = Query(...),
        model_suid: int = Query(...),
    ):
        model = self._find(request, server_id, model_suid)

        if model is None:
            return JSONResponse({"error": "Server not found"}, status_code=404)

        if isinstance(model, RemoteModelProxy):
            return model.get_status()

        return self._status_response(model)

    async def _send_command(
        self,
        request: Request,
        server_id: str,
        model_suid: int,
        command: str,
    ):
        model = self._find(request, server_id, model_suid)

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

    def _find(self, request: Request, server_id: str, model_suid: int) -> LocalManagedModel | RemoteModelProxy | None:
        for key, local_model in self.manager.get_local_models().items():
            if local_model.get_server_identifier() == server_id and int(key) == model_suid:
                return local_model
        for remote_model in self.manager.get_remote_models():
            if remote_model.server_id == server_id and remote_model.remote_model_index == model_suid:
                return remote_model
        return None

    @staticmethod
    def _status_response(local_model: LocalManagedModel):
        status = local_model.get_status()
        if status["state"] == "error":
            lines = local_model.log_buffer.snapshot()
            status["error"] = lines[-1].text if lines else "Unknown error"
            return JSONResponse(status, status_code=500)
        return status
