from __future__ import annotations

from .config import ModelConfig
from .model import ModelIdentifier


class RemoteUnmanagedModel:
    """Represents a remotely-hosted llama-server configured via `type: remote` in ModelConfig.

    Unlike ProcessManager (locally spawned) or RemoteModelProxy (proxied via
    an uplink RemoteManagerClient), this server is neither managed nor
    monitored by this instance.  It is simply a known address we can route
    requests to and poll for health/slot data.
    """

    def __init__(
        self,
        model_index: int,
        manager_id: str,
        config: ModelConfig,
    ) -> None:
        assert config.type == "remote", (
            f"RemoteUnmanagedServer requires type='remote', got {config.type!r}"
        )

        self.model_index = model_index
        self.name: str | None = config.name
        self.model_id: str = config.effective_id
        self.remote_address: str = config.remote_address.rstrip("/")
        self.remote_model_id: str | None = config.remote_model_id

        self._model_identifier = ModelIdentifier(
            manager_id=manager_id,
            process_identifier=str(model_index),
        )

    # ------------------------------------------------------------------
    # Duck-type interface shared with ProcessManager / RemoteModelProxy
    # ------------------------------------------------------------------

    def get_server_identifier(self) -> str:
        return str(self._model_identifier)

    def get_server_address(self) -> str:
        return self.remote_address

    def get_status(self) -> dict:
        return {
            "state": "remote",
            "pid": None,
            "host": None,
            "port": None,
            "uptime": None,
        }

    def get_prompt_progress(self) -> dict:
        return {}
