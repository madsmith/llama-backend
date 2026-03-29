from __future__ import annotations

import time

from llama_manager.event_bus import EventBus
from llama_manager.log_buffer import LogBuffer
from llama_manager.model import ModelIdentifier
from llama_manager.manager.backends.local_managed import ServerState
from llama_manager.protocol.backend import ManagedBackend, RemoteClient


class RemoteModelProxy(ManagedBackend):
    """ManagedBackend proxy for a model hosted on a remote LlamaManager."""

    def __init__(
        self,
        remote_index: int,
        remote_model_index: int,
        name: str | None,
        model_id: str,
        proxy_url: str,
        server_id: str,
        model_identifier: ModelIdentifier,
        client: RemoteClient,
        event_bus: EventBus,
        log_buffer_size: int = 10_000,
    ) -> None:
        self.remote_index = remote_index
        self.remote_model_index = remote_model_index
        self.name = name
        self.model_id: str = model_id
        self.proxy_url = proxy_url
        self.server_id = server_id
        self.model_identifier = model_identifier
        self._client = client
        self._event_bus = event_bus
        self.state: ServerState = ServerState.unknown
        self._started_at: float | None = None
        self.log_buffer = LogBuffer(maxlen=log_buffer_size)
        self._cached_slots: list[dict] | None = None
        self._cached_health: dict | None = None
        self.llama_server_port: int | None = None

    # ------------------------------------------------------------------
    # Backend protocol
    # ------------------------------------------------------------------

    def get_suid(self) -> str:
        return self.server_id

    def get_name(self) -> str | None:
        return self.name

    def get_base_url(self) -> str:
        return self.proxy_url

    def get_model_ids(self) -> list[str]:
        return [self.model_id]

    def is_available(self) -> bool:
        return self.state == ServerState.running

    async def get_slots(self) -> list[dict] | None:
        if self._cached_slots is not None:
            return self._cached_slots
        slots = await self._client.request_slots(self.remote_model_index)
        if slots:
            self._cached_slots = slots
        return slots

    async def get_health(self) -> dict:
        if self._cached_health is not None:
            return self._cached_health
        health = await self._client.request_health(self.remote_model_index)
        if health is not None:
            self._cached_health = health
            return health
        return {"status": "unknown"}

    # ------------------------------------------------------------------
    # ManagedBackend protocol
    # ------------------------------------------------------------------

    def get_log_buffer(self) -> LogBuffer:
        return self.log_buffer

    def get_status(self) -> dict:
        is_running = self.state == ServerState.running
        uptime = (time.time() - self._started_at) if is_running and self._started_at is not None else None
        return {
            "state": self.state.value,
            "pid": None,
            "host": self._client.get_config().host if is_running else None,
            "port": self.llama_server_port if is_running else None,
            "uptime": uptime,
        }

    async def start(self) -> None:
        await self.send_command("start")

    async def stop(self) -> None:
        await self.send_command("stop")

    async def restart(self) -> None:
        await self.send_command("restart")

    # ------------------------------------------------------------------
    # Push interface called by RemoteManagerClient
    # ------------------------------------------------------------------

    def set_state(self, state_str: str) -> None:
        prev = self.state
        try:
            self.state = ServerState(state_str)
        except ValueError:
            self.state = ServerState.error
        if self.state == ServerState.running and prev != ServerState.running:
            self._started_at = time.time()
        elif self.state != ServerState.running:
            self._started_at = None
        self._event_bus.publish({"type": "server_status", "id": self.server_id, "data": {"state": self.state.value}})

    def feed_log(self, text: str) -> None:
        line = self.log_buffer.append(text)
        self._event_bus.publish({"type": "server_log", "id": self.server_id, "data": {"line_id": line.id, "text": line.text}})

    def set_slots(self, slots: list) -> None:
        self._cached_slots = slots

    def set_health(self, health: dict) -> None:
        self._cached_health = health

    async def send_command(self, cmd: str) -> None:
        await self._client.send_command(self.remote_model_index, cmd)
