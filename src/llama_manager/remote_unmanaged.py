from __future__ import annotations

from .config import ModelConfig


class RemoteUnmanagedModel:
    """Represents a remotely-hosted llama-server configured via `type: remote` in ModelConfig.

    Unlike LocalManagedModel (locally spawned) or RemoteModelProxy (proxied via
    an uplink RemoteManagerClient), this server is neither managed nor
    monitored by this instance.  It is simply a known address we can route
    requests to and poll for health/slot data.
    """

    def __init__(
        self,
        server_id: str,
        config: ModelConfig,
    ) -> None:
        assert config.type == "remote", (
            f"RemoteUnmanagedServer requires type='remote', got {config.type!r}"
        )

        self._server_id = server_id
        self.name: str | None = config.name
        self.model_id: str = config.effective_id
        self.remote_address: str = config.remote_address.rstrip("/")
        self.remote_model_id: str | None = config.remote_model_id

    # ------------------------------------------------------------------
    # Duck-type interface shared with LocalManagedModel / RemoteModelProxy
    # ------------------------------------------------------------------

    def get_server_identifier(self) -> str:
        return self._server_id

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
