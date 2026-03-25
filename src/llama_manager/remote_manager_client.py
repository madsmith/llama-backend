from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import websockets

from .config import AppConfig, RemoteManagerConfig
from .event_bus import bus as event_bus
from .log_buffer import LogBuffer
from .model import ModelIdentifier
from .process_manager import ServerState

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger(__name__)


class RemoteModelProxy:
    """Mirrors the ProcessManager interface for a model on a remote manager."""

    def __init__(
        self,
        local_index: int,
        remote_index: int,
        remote_model_index: int,
        name: str | None,
        model_id: str | None,
        proxy_url: str,
        server_id: str,
        model_identifier: ModelIdentifier,
        client: RemoteManagerClient,
        log_buffer_size: int = 10_000,
    ) -> None:
        self.local_index = local_index
        self.remote_index = remote_index
        self.remote_model_index = remote_model_index
        self.name = name
        self.model_id = model_id
        self.proxy_url = proxy_url
        self.server_id = server_id
        self.model_identifier = model_identifier
        self._client = client
        self.state: ServerState = ServerState.stopped
        self.log_buffer = LogBuffer(maxlen=log_buffer_size)
        self._subscribers: list[asyncio.Queue[dict]] = []
        self._cached_slots: list[dict] = []
        self._cached_health: dict | None = None
        self.llama_server_port: int | None = None

    # --- ProcessManager duck-type interface ---

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def shutdown_subscribers(self) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait({})
            except asyncio.QueueFull:
                pass

    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "pid": None,
            "host": None,
            "port": None,
            "uptime": None,
        }

    def get_prompt_progress(self) -> dict:
        return {}

    def get_cached_slots(self) -> list[dict]:
        return self._cached_slots

    def get_cached_health(self) -> dict | None:
        return self._cached_health

    # --- Push interface called by RemoteManagerClient ---

    def _broadcast(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def set_state(self, state_str: str) -> None:
        try:
            self.state = ServerState(state_str)
        except ValueError:
            self.state = ServerState.error
        self._broadcast({"type": "state", "state": self.state.value})

    def feed_log(self, text: str) -> None:
        line = self.log_buffer.append(text)
        self._broadcast({"type": "log", "id": line.id, "text": line.text})

    def set_slots(self, slots: list) -> None:
        self._cached_slots = slots

    def set_health(self, health: dict) -> None:
        self._cached_health = health

    async def send_command(self, cmd: str) -> None:
        await self._client.send_command(self.remote_model_index, cmd)


class RemoteManagerClient:
    """Connects to a remote Llama Manager over WebSocket and proxies its models."""

    def __init__(
        self,
        remote_index: int,
        config: RemoteManagerConfig,
        app_config: AppConfig,
        app: FastAPI,
    ) -> None:
        self.remote_index = remote_index
        self.config = config
        self._app_config = app_config
        self.app = app
        self.models: list[RemoteModelProxy] = []
        self.connection_state: str = "disconnected"
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self.server_id: str | None = None

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._ws is not None:
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._remove_proxied_models()

    def _remove_proxied_models(self) -> None:
        pms: list = getattr(self.app.state, "process_managers", [])
        for proxy in self.models:
            proxy.shutdown_subscribers()
            if proxy.local_index < len(pms) and pms[proxy.local_index] is proxy:
                pms[proxy.local_index] = None
        # Trim trailing Nones that are past the local models zone
        local_count = len(self._app_config.models)
        while len(pms) > local_count and pms[-1] is None:
            pms.pop()
        self.models.clear()

    def _ws_url(self) -> str:
        return f"{self.config.ws_url}?token={self.config.token}"

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_serve()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("Remote manager [%d] error: %r", self.remote_index, exc)

            if self._stop_event.is_set():
                break

            self.connection_state = "disconnected"
            for proxy in self.models:
                proxy.set_state("error")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.reconnect_interval,
                )
            except asyncio.TimeoutError:
                pass

        self.connection_state = "disconnected"

    async def _connect_and_serve(self) -> None:
        url = self._ws_url()
        log.debug("Remote manager [%d] connecting to %s", self.remote_index, url)
        self.connection_state = "connecting"
        async with websockets.connect(url) as ws:
            self._ws = ws
            self.connection_state = "connected"
            log.debug("Remote manager [%d] connected", self.remote_index)
            async for raw in ws:
                if self._stop_event.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self._handle_message(msg)
        self._ws = None
        self.connection_state = "disconnected"

    async def _handle_message(self, msg: dict) -> None:
        t = msg.get("type")

        if t == "snapshot":
            proxy_port = msg.get("proxy_port", 1234)
            remote_manager_id = msg.get("manager_id", "")
            self.server_id = remote_manager_id
            await self._reconcile_models(msg.get("models", []), proxy_port, remote_manager_id)

        elif t == "state":
            proxy = self._get_proxy(msg.get("model", 0))
            if proxy:
                proxy.set_state(msg.get("state", "error"))

        elif t == "log":
            proxy = self._get_proxy(msg.get("model", 0))
            if proxy:
                proxy.feed_log(msg.get("text", ""))

        elif t == "log_history":
            proxy = self._get_proxy(msg.get("model", 0))
            if proxy:
                for entry in msg.get("lines", []):
                    proxy.feed_log(entry.get("text", ""))

        elif t == "slots":
            proxy = self._get_proxy(msg.get("model", 0))
            if proxy:
                slots = msg.get("slots", [])
                proxy.set_slots(slots)
                event_bus.publish({"type": "slots", "server_id": proxy.server_id, "slots": slots})

        elif t == "health":
            proxy = self._get_proxy(msg.get("model", 0))
            if proxy:
                health = msg.get("health")
                if health is not None:
                    proxy.set_health(health)
                    event_bus.publish({"type": "health", "server_id": proxy.server_id, "health": health})

    def _get_proxy(self, remote_model_index: int) -> RemoteModelProxy | None:
        for p in self.models:
            if p.remote_model_index == remote_model_index:
                return p
        return None

    async def _reconcile_models(self, model_descriptors: list[dict], proxy_port: int = 1234, remote_manager_id: str = "") -> None:
        pms: list = self.app.state.process_managers
        log_buffer_size = self._app_config.web_ui.log_buffer_size
        proxy_url = f"http://{self.config.host}:{proxy_port}"

        # Build a stable key->proxy map from existing models
        existing: dict[str, RemoteModelProxy] = {}
        for p in self.models:
            key = p.name or f"__idx_{p.remote_model_index}"
            existing[key] = p

        new_models: list[RemoteModelProxy] = []
        for desc in model_descriptors:
            rmi = desc.get("index", len(new_models))
            name = desc.get("name")
            model_id = desc.get("model_id")
            server_id = desc.get("server_id") or f"{self.config.host}:model-{rmi}"
            state_str = desc.get("state", "stopped")
            llama_port: int | None = desc.get("llama_port")
            key = name or f"__idx_{rmi}"

            # Construct ModelIdentifier: prefer parsing server_id, fall back to remote_manager_id
            if remote_manager_id:
                process_identifier = desc.get("process_identifier") or f"model-{rmi}"
                model_identifier = ModelIdentifier(remote_manager_id, process_identifier)
            else:
                try:
                    model_identifier = ModelIdentifier.from_string(server_id)
                except ValueError:
                    model_identifier = ModelIdentifier(self.config.host, f"model-{rmi}")

            if key in existing:
                proxy = existing.pop(key)
                proxy.remote_model_index = rmi
                proxy.name = name
                proxy.model_id = model_id
                proxy.proxy_url = proxy_url
                proxy.server_id = server_id
                proxy.model_identifier = model_identifier
            else:
                # Find a free slot at or beyond the local models zone
                local_count = len(self._app_config.models)
                local_index = len(pms)
                for i in range(local_count, len(pms)):
                    if pms[i] is None:
                        local_index = i
                        break
                proxy = RemoteModelProxy(
                    local_index=local_index,
                    remote_index=self.remote_index,
                    remote_model_index=rmi,
                    name=name,
                    model_id=model_id,
                    proxy_url=proxy_url,
                    server_id=server_id,
                    model_identifier=model_identifier,
                    client=self,
                    log_buffer_size=log_buffer_size,
                )
                if local_index < len(pms):
                    pms[local_index] = proxy
                else:
                    pms.append(proxy)

            proxy.llama_server_port = llama_port

            # Apply state from snapshot
            try:
                proxy.state = ServerState(state_str)
            except ValueError:
                proxy.state = ServerState.error

            new_models.append(proxy)

        # Remove models that disappeared from the remote
        for old_proxy in existing.values():
            old_proxy.shutdown_subscribers()
            if old_proxy.local_index < len(pms) and pms[old_proxy.local_index] is old_proxy:
                pms[old_proxy.local_index] = None

        self.models = new_models

    async def send_command(self, remote_model_index: int, cmd: str) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": cmd, "model": remote_model_index}))
        except Exception as exc:
            log.debug("Failed to send command to remote manager [%d]: %r", self.remote_index, exc)
