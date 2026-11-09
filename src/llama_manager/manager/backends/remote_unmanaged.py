from __future__ import annotations

from urllib.parse import urlparse

from llama_manager.config import ModelConfig
from llama_manager.manager.backend_probe import BackendInfo, VllmMetricsReader, detect_backend
from llama_manager.protocol.backend import Backend, LlamaManagerProtocol


class RemoteUnmanagedModel(Backend):
    """Represents a remotely-hosted llama-server configured via `type: remote` in ModelConfig.

    Unlike LocalManagedModel (locally spawned) or RemoteModelProxy (proxied via
    an uplink RemoteManagerClient), this server is neither managed nor
    monitored by this instance.  It is simply a known address we can route
    requests to and poll for health/slot data.
    """

    def __init__(self, config: ModelConfig, manager: LlamaManagerProtocol) -> None:
        assert config.type == "remote", (
            f"RemoteUnmanagedServer requires type='remote', got {config.type!r}"
        )
        self._manager = manager
        self._model_suid: str = config.suid
        self._name: str | None = config.name
        self._model_id: str = config.effective_id
        self._base_url: str = config.remote_address.rstrip("/")
        self._remote_model_id: str | None = config.remote_model_id
        parsed = urlparse(self._base_url)
        self._host: str | None = parsed.hostname
        self._port: int | None = parsed.port
        self._supports_slots: bool | None = None
        self._backend: BackendInfo | None = None
        self._vllm_metrics = VllmMetricsReader()

    # ------------------------------------------------------------------
    # Backend protocol
    # ------------------------------------------------------------------

    def get_manager_id(self) -> str:
        return self._manager.get_manager_id()

    def get_suid(self) -> str:
        return self._model_suid

    def get_name(self) -> str | None:
        return self._name

    def get_base_url(self) -> str:
        return self._base_url

    def get_model_ids(self) -> list[str]:
        return [self._model_id]

    def map_model_id(self, model_id: str | None) -> str | None:
        return self._remote_model_id or model_id

    def is_available(self) -> bool:
        return True

    async def get_slots(self) -> list[dict] | None:
        if self._supports_slots is False:
            return None

        slots = await self._manager.get_client_at(self._base_url).get_slots()
        if self._supports_slots is None:
            self._supports_slots = slots is not None
        return slots

    def get_status(self) -> dict:
        return {"state": "remote", "pid": None, "host": self._host, "port": self._port, "uptime": None}

    async def get_health(self) -> dict:
        """Health plus backend type, and for vLLM a metrics summary (it has no /slots).

        Unreachable servers report "offline"; detection is redone after an outage
        in case a different server came back on the same address.
        """
        health = await self._manager.get_client_at(self._base_url).get_health()
        if health is None:
            self._backend = None
            self._supports_slots = None
            return {"status": "offline"}

        if self._backend is None:
            self._backend = await detect_backend(self._base_url)

        result = dict(health)
        if self._backend is not None:
            result["backend"] = self._backend.kind
            if self._backend.version:
                result["version"] = self._backend.version
            if self._backend.kind == "vllm":
                metrics = await self._vllm_metrics.read(self._base_url)
                if metrics is not None:
                    result["metrics"] = metrics
        return result

    async def ensure_ready(self, jit_enabled: bool, timeout: float) -> None:
        return  # unmanaged — lifecycle is not our responsibility
