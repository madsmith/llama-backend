from __future__ import annotations

import asyncio
import json
import logging

import websockets

from llama_manager.config import AppConfig, RemoteManagerConfig
from llama_manager.event_bus import EventBus
from llama_manager.model import ModelIdentifier
from llama_manager.manager.backends import RemoteModelProxy

logger = logging.getLogger(__name__)


class RemoteManagerClient:
    """Connects to a remote Llama Manager over WebSocket and proxies its models."""

    def __init__(
        self,
        remote_index: int,
        config: RemoteManagerConfig,
        app_config: AppConfig,
        event_bus: EventBus,
    ) -> None:
        self.remote_index = remote_index
        self._config = config
        self._app_config = app_config
        self._event_bus = event_bus
        self.models: list[RemoteModelProxy] = []
        self.connection_state: str = "disconnected"
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self.server_id: str | None = None
        self._pending_requests: dict[str, asyncio.Future[list[dict]]] = {}
        self._request_counter: int = 0

    def get_config(self) -> RemoteManagerConfig:
        return self._config

    def set_config(self, config: RemoteManagerConfig) -> None:
        self._config = config

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

    def _ws_url(self) -> str:
        return f"{self._config.ws_url}?token={self._config.token}"

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_serve()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Remote manager [%d] error: %r", self.remote_index, exc)

            if self._stop_event.is_set():
                break

            self.connection_state = "disconnected"
            for proxy in self.models:
                proxy.set_state("error")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.reconnect_interval,
                )
            except asyncio.TimeoutError:
                pass

        self.connection_state = "disconnected"

    async def _connect_and_serve(self) -> None:
        url = self._ws_url()
        logger.debug("Remote manager [%d] connecting to %s", self.remote_index, url)
        self.connection_state = "connecting"
        async with websockets.connect(url) as ws:
            self._ws = ws
            self.connection_state = "connected"
            logger.debug("Remote manager [%d] connected", self.remote_index)
            async for raw in ws:
                if self._stop_event.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self._handle_message(msg)
        self._ws = None
        for fut in list(self._pending_requests.values()):
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()
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
                self._event_bus.publish({"type": "slots", "server_id": proxy.server_id, "slots": slots})

        elif t == "health":
            proxy = self._get_proxy(msg.get("model", 0))
            if proxy:
                health = msg.get("health")
                if health is not None:
                    proxy.set_health(health)
                    self._event_bus.publish({"type": "health", "server_id": proxy.server_id, "health": health})

        elif t == "slots_response":
            request_id = msg.get("request_id", "")
            future = self._pending_requests.get(request_id)
            if future is not None and not future.done():
                future.set_result(msg.get("slots", []))

        elif t == "health_response":
            request_id = msg.get("request_id", "")
            future = self._pending_requests.get(request_id)
            if future is not None and not future.done():
                future.set_result(msg.get("health"))

    def _get_proxy(self, remote_model_index: int) -> RemoteModelProxy | None:
        for p in self.models:
            if p.remote_model_index == remote_model_index:
                return p
        return None

    async def _reconcile_models(self, model_descriptors: list[dict], proxy_port: int = 1234, remote_manager_id: str = "") -> None:
        log_buffer_size = self._app_config.web_ui.log_buffer_size
        proxy_url = f"http://{self._config.host}:{proxy_port}"

        existing: dict[str, RemoteModelProxy] = {}
        for p in self.models:
            key = p.name or f"__idx_{p.remote_model_index}"
            existing[key] = p

        new_models: list[RemoteModelProxy] = []
        for desc in model_descriptors:
            rmi = desc.get("index", len(new_models))
            name = desc.get("name")
            model_id = desc.get("model_id")
            server_id = desc.get("server_id") or f"{self._config.host}:model-{rmi}"
            state_str = desc.get("state", "stopped")
            llama_port: int | None = desc.get("llama_port")
            key = name or f"__idx_{rmi}"
            # TODO: add pydantic validation for model descriptors

            if name is None:
                logger.warning(f"Model descriptor missing name: {desc}")
                name = f'Model {rmi}'

            if model_id is None:
                logger.error(f"Model descriptor missing model_id [skipping]: {desc}")
                continue

            if remote_manager_id:
                process_identifier = desc.get("process_identifier") or f"model-{rmi}"
                model_identifier = ModelIdentifier(remote_manager_id, process_identifier)
            else:
                try:
                    model_identifier = ModelIdentifier.from_string(server_id)
                except ValueError:
                    model_identifier = ModelIdentifier(self._config.host, f"model-{rmi}")

            if key in existing:
                proxy = existing.pop(key)
                proxy.remote_model_index = rmi
                proxy.name = name
                proxy.model_id = model_id
                proxy.proxy_url = proxy_url
                proxy.server_id = server_id
                proxy.model_identifier = model_identifier
            else:
                proxy = RemoteModelProxy(
                    remote_index=self.remote_index,
                    remote_model_index=rmi,
                    name=str(name),
                    model_id=str(model_id),
                    proxy_url=proxy_url,
                    server_id=server_id,
                    model_identifier=model_identifier,
                    client=self,
                    event_bus=self._event_bus,
                    log_buffer_size=log_buffer_size,
                )

            proxy.llama_server_port = llama_port
            proxy.set_state(state_str)
            new_models.append(proxy)

        self.models = new_models

    async def send_command(self, remote_model_index: int, cmd: str) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": cmd, "model": remote_model_index}))
        except Exception as exc:
            logger.debug("Failed to send command to remote manager [%d]: %r", self.remote_index, exc)

    async def request_health(self, remote_model_index: int) -> dict | None:
        """Send a get_health request and await the response, returning None on timeout or error."""
        if self._ws is None:
            return None
        self._request_counter += 1
        request_id = str(self._request_counter)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "type": "get_health",
                "model": remote_model_index,
                "request_id": request_id,
            }))
            return await asyncio.wait_for(future, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    async def request_slots(self, remote_model_index: int) -> list[dict]:
        """Send a get_slots request and await the response, returning [] on timeout or error."""
        if self._ws is None:
            return []
        self._request_counter += 1
        request_id = str(self._request_counter)
        future: asyncio.Future[list[dict]] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "type": "get_slots",
                "model": remote_model_index,
                "request_id": request_id,
            }))
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            logger.debug("Failed to receive get_slots response: %r", e)
            return []
        finally:
            self._pending_requests.pop(request_id, None)
