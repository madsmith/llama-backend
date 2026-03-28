from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from llama_manager.llama_manager import LlamaManager
from llama_manager.process_manager import ProcessManager
from llama_manager.remote_manager_client import RemoteModelProxy

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
        pm = self._find(request, server_id, model_suid)

        if pm is None:
            return JSONResponse({"error": "Server not found"}, status_code=404)

        if isinstance(pm, RemoteModelProxy):
            return pm.get_status()

        return self._status_response(pm)


    async def _send_command(
        self, 
        request: Request, 
        server_id: str, 
        model_suid: int, 
        command: str
    ):
        pm: ProcessManager | RemoteModelProxy = self._find(request, server_id, model_suid)

        if pm is None:
            return JSONResponse({"error": "Server not found"}, status_code=404)
        
        if isinstance(pm, RemoteModelProxy):
            await pm.send_command(command)
            return pm.get_status()
        
        command_fn = getattr(pm, command, None)
        if command_fn is None:
            return JSONResponse({"error": "Command not found"}, status_code=404)
        
        await command_fn()

        return self._status_response(pm)


    def _find(self, request: Request, server_id: str, model_suid: int) -> ProcessManager | RemoteModelProxy | None:
        for i_str, pm in self.manager.get_process_managers().items():
            if pm.get_server_identifier() == server_id and int(i_str) == model_suid:
                return pm
        for model in self.manager.get_remote_models():
            if model.server_id == server_id and model.remote_model_index == model_suid:
                return model
        return None


    def _status_response(process_manager):
        s = process_manager.get_status()
        if s["state"] == "error":
            lines = process_manager.log_buffer.snapshot()
            s["error"] = lines[-1].text if lines else "Unknown error"
            return JSONResponse(s, status_code=500)
        return s





