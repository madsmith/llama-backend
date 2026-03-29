from __future__ import annotations

from llama_manager.config import ModelConfig
from llama_manager.llama_client import LlamaClient
from llama_manager.protocol.backend import Backend


class RemoteUnmanagedModel(Backend):
    """Represents a remotely-hosted llama-server configured via `type: remote` in ModelConfig.

    Unlike LocalManagedModel (locally spawned) or RemoteModelProxy (proxied via
    an uplink RemoteManagerClient), this server is neither managed nor
    monitored by this instance.  It is simply a known address we can route
    requests to and poll for health/slot data.
    """

    def __init__(self, config: ModelConfig, server_id: str) -> None:
        assert config.type == "remote", (
            f"RemoteUnmanagedServer requires type='remote', got {config.type!r}"
        )
        self._server_id = server_id
        self._name: str | None = config.name
        self._model_id: str = config.effective_id
        self._base_url: str = config.remote_address.rstrip("/")
        self._remote_model_id: str | None = config.remote_model_id

    # ------------------------------------------------------------------
    # Backend protocol
    # ------------------------------------------------------------------

    def get_suid(self) -> str:
        return self._server_id

    def get_name(self) -> str | None:
        return self._name

    def get_base_url(self) -> str:
        return self._base_url

    def get_model_ids(self) -> list[str]:
        return [self._model_id]

    def is_available(self) -> bool:
        return True

    async def get_slots(self) -> list[dict] | None:
        return await LlamaClient(self._base_url).get_slots()

    async def get_health(self) -> dict:
        return await LlamaClient(self._base_url).get_health() or {"status": "unknown"}
