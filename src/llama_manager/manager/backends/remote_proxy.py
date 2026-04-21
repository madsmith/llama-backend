from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

from llama_manager.util.event_bus import EventBus
from llama_manager.util.log_buffer import LogBuffer
from llama_manager.protocol.ws_messages import LogRecord as WireLogRecord
from llama_manager.manager.backends.local_managed import ServerState
from llama_manager.protocol.backend import ManagedBackend, RemoteClient


class RemoteModelProxy(ManagedBackend):
    """ManagedBackend proxy for a model hosted on a remote LlamaManager."""

    def __init__(
        self,
        manager_id: str,
        suid: str,
        name: str | None,
        model_id: str,
        proxy_url: str,
        client: RemoteClient,
        event_bus: EventBus,
        log_buffer_size: int = 10_000,
    ) -> None:
        self._manager_id = manager_id
        self._suid = suid
        self.name = name
        self.model_id: str = model_id
        self.proxy_url = proxy_url
        self._client = client
        self._event_bus = event_bus
        self.state: ServerState = ServerState.unknown
        self._started_at: float | None = None
        self._pid: int | None = None
        self._host: str | None = None
        self._port: int | None = None
        self.auto_start: bool = False
        self.has_ttl: bool = False
        self.allow_proxy: bool = True
        self.log_buffer = LogBuffer(self.get_manager_id(), maxlen=log_buffer_size)
        self._cached_slots: list[dict] | None = None
        self._cached_health: dict | None = None
        self.llama_server_port: int | None = None

    # ------------------------------------------------------------------
    # Backend protocol
    # ------------------------------------------------------------------

    def get_manager_id(self) -> str:
        return self._manager_id

    def get_suid(self) -> str:
        return self._suid

    def get_name(self) -> str | None:
        return self.name

    def get_base_url(self) -> str:
        return self.proxy_url

    def get_model_ids(self) -> list[str]:
        return [self.model_id]

    def map_model_id(self, model_id: str | None) -> str | None:
        return model_id

    def is_available(self) -> bool:
        return self.state == ServerState.running

    async def get_slots(self) -> list[dict] | None:
        if self._cached_slots is not None:
            return self._cached_slots
        slots = await self._client.request_slots(self._suid)
        if slots:
            self._cached_slots = slots
        return slots

    async def get_health(self) -> dict:
        if self._cached_health is not None:
            return self._cached_health
        health = await self._client.request_health(self._suid)
        if health is not None:
            self._cached_health = health
            return health
        return {"status": "unknown"}

    async def get_props(self) -> dict | None:
        return await self._client.request_props(self._suid)

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
            "pid": self._pid if is_running else None,
            "host": self._host if is_running else None,
            "port": self._port if is_running else None,
            "uptime": uptime,
        }

    async def ensure_ready(self, jit_enabled: bool, timeout: float) -> None:
        if self.state == ServerState.running:
            return

        name = self.name or self.get_suid()
        if self.state == ServerState.error:
            raise RuntimeError(f"Remote model [{name}] is in error state")
        if self.state == ServerState.stopped:
            if not jit_enabled:
                raise RuntimeError(f"Remote model [{name}] is not running")
            await self.send_command("start")

        # state is starting, or we just sent start — wait for running or error
        elapsed = 0.0
        while elapsed < timeout:
            if self.state == ServerState.running:
                return
            if self.state == ServerState.error:
                raise RuntimeError(f"Remote model [{name}] failed to start")
            await asyncio.sleep(0.5)
            elapsed += 0.5

        raise RuntimeError(f"Remote model [{name}] did not become ready within {timeout}s")

    async def start(self) -> None:
        await self.send_command("start")

    async def stop(self) -> None:
        await self.send_command("stop")

    async def restart(self) -> None:
        await self.send_command("restart")

    # ------------------------------------------------------------------
    # Push interface called by RemoteManagerClient
    # ------------------------------------------------------------------

    def set_status(self, status: dict) -> None:
        """Synchronise state from a full status dict (as produced by LocalManagedModel.get_status)."""
        state_str = status.get("state", "error")
        prev = self.state
        try:
            self.state = ServerState(state_str)
        except ValueError:
            self.state = ServerState.error

        if self.state == ServerState.running:
            self._pid = status.get("pid")
            self._host = status.get("host")
            self._port = status.get("port")
            if prev != ServerState.running or self._started_at is None:
                uptime = status.get("uptime")
                self._started_at = (time.time() - uptime) if uptime is not None else time.time()
        else:
            self._pid = None
            self._host = None
            self._port = None
            self._started_at = None

        self._event_bus.publish({"type": "server_status", "id": self._suid, "data": {"state": self.state.value}})

    def set_state(self, state_str: str) -> None:
        """Convenience for simple state-only resets (e.g. error on disconnect)."""
        self.set_status({"state": state_str})

    def feed_log(self, text: str, line_number: int | None = None) -> None:
        line = self.log_buffer.append(text)
        data = WireLogRecord.from_buffer(line).model_dump()
        if line_number is not None:
            # Use the remote manager's line_number so the frontend's dedup logic
            # stays consistent with fetch_log, which also returns remote numbers.
            data["line_number"] = line_number
        self._event_bus.publish({"type": "server_log", "id": self._suid, "data": data})

    def set_slots(self, slots: list) -> None:
        self._cached_slots = slots

    def set_health(self, health: dict) -> None:
        self._cached_health = health

    async def fetch_log(
        self, before_id: str | None, limit: int
    ) -> tuple[list[dict], bool] | None:
        """Fetch a page of log records from the uplink.

        Returns (lines, has_more) or None if the uplink is unreachable.
        Lines are dicts in the LogRecord wire format.
        """
        return await self._client.request_log(self._suid, before_id, limit)

    async def send_command(self, cmd: str) -> None:
        await self._client.send_command(self._suid, cmd)
