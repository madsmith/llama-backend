from __future__ import annotations

import asyncio
import json
import logging

import websockets

from llama_manager.config import AppConfig, RemoteManagerConfig
from llama_manager.util.event_bus import EventBus
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
        self._manager_id: str = ""
        self._pending_requests: dict[str, asyncio.Future[list[dict]]] = {}
        self._request_counter: int = 0
        self._logged_connection_error: bool = False

    def get_config(self) -> RemoteManagerConfig:
        return self._config

    def set_config(self, config: RemoteManagerConfig) -> None:
        self._config = config

    def get_manager_id(self) -> str:
        assert self._manager_id, "Manager ID not set"
        return self._manager_id

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
        return self._config.ws_url

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_serve()
            except asyncio.CancelledError:
                break
            except websockets.ConnectionClosed as exc:
                # Expected closes (service restart, going away) are informational.
                # Only warn the first time for codes that indicate a real problem.
                code = exc.rcvd.code if exc.rcvd is not None else None
                expected = code in (1000, 1001, 1012)
                if expected or self._logged_connection_error:
                    logger.debug("Remote manager [%d] connection closed: %r", self.remote_index, exc)
                else:
                    logger.warning("Remote manager [%d] connection error: %r", self.remote_index, exc)
                    self._logged_connection_error = True
            except Exception as exc:
                if not self._logged_connection_error:
                    logger.warning("Remote manager [%d] connection error: %r", self.remote_index, exc)
                    self._logged_connection_error = True
                else:
                    logger.debug("Remote manager [%d] connection error: %r", self.remote_index, exc)

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
            if self._logged_connection_error:
                logger.info("Remote manager [%d] connected", self.remote_index)
                self._logged_connection_error = False
            else:
                logger.debug("Remote manager [%d] connected", self.remote_index)

            # Authenticate
            await ws.send(json.dumps({"type": "authenticate", "token": self._config.token}))
            try:
                auth_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                auth_msg = json.loads(auth_raw)
            except Exception as exc:
                logger.warning("Remote manager [%d]: auth error: %r", self.remote_index, exc)
                return

            if auth_msg.get("type") != "authenticate_response" or not auth_msg.get("success"):
                logger.warning(
                    "Remote manager [%d]: authentication failed: %s",
                    self.remote_index,
                    auth_msg.get("reason", "unknown"),
                )
                return

            self._manager_id = auth_msg.get("manager_id", "")
            logger.debug("Remote manager [%d] authenticated, manager_id=%s", self.remote_index, self._manager_id)

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
            await self._reconcile_models(msg.get("models", []), proxy_port)

        elif t == "state":
            proxy = self._get_proxy(msg.get("suid", ""))
            if proxy:
                proxy.set_status(msg)

        elif t == "log":
            proxy = self._get_proxy(msg.get("suid", ""))
            if proxy:
                proxy.feed_log(msg.get("text") or "")

        elif t == "log_response":
            request_id = msg.get("request_id", "")
            future = self._pending_requests.get(request_id)
            if future is not None and not future.done():
                future.set_result((msg.get("lines", []), msg.get("has_more", False)))

        elif t == "slots":
            proxy = self._get_proxy(msg.get("suid", ""))
            if proxy:
                slots = msg.get("slots", [])
                proxy.set_slots(slots)
                self._event_bus.publish({"type": "slots", "id": proxy.get_suid(), "data": {"slots": slots}})

        elif t == "health":
            proxy = self._get_proxy(msg.get("suid", ""))
            if proxy:
                health = msg.get("health")
                if health is not None:
                    proxy.set_health(health)
                    self._event_bus.publish({"type": "health", "id": proxy.get_suid(), "data": {"health": health}})

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

        elif t == "props_response":
            request_id = msg.get("request_id", "")
            future = self._pending_requests.get(request_id)
            if future is not None and not future.done():
                future.set_result(msg.get("props"))

    def _get_proxy(self, suid: str) -> RemoteModelProxy | None:
        for p in self.models:
            if p.get_suid() == suid:
                return p
        return None

    async def _reconcile_models(self, model_descriptors: list[dict], proxy_port: int = 1234) -> None:
        log_buffer_size = self._app_config.web_ui.log_buffer_size
        proxy_url = f"http://{self._config.host}:{proxy_port}"

        existing: dict[str, RemoteModelProxy] = {p.get_suid(): p for p in self.models}

        new_models: list[RemoteModelProxy] = []
        for desc in model_descriptors:
            suid = desc.get("suid", "")
            name = desc.get("name")
            model_id = desc.get("model_id")
            state_str = desc.get("state", "stopped")
            llama_port: int | None = desc.get("llama_port")

            if not suid:
                logger.warning("Model descriptor missing suid: %s", desc)
                continue

            if model_id is None:
                logger.error("Model descriptor missing model_id [skipping]: %s", desc)
                continue

            if suid in existing:
                proxy = existing.pop(suid)
                proxy.name = name
                proxy.model_id = str(model_id)
                proxy.proxy_url = proxy_url
            else:
                proxy = RemoteModelProxy(
                    manager_id=self._manager_id,
                    suid=suid,
                    name=name,
                    model_id=str(model_id),
                    proxy_url=proxy_url,
                    client=self,
                    event_bus=self._event_bus,
                    log_buffer_size=log_buffer_size,
                )

            proxy.llama_server_port = llama_port
            proxy.auto_start = bool(desc.get("auto_start", False))
            proxy.has_ttl = bool(desc.get("has_ttl", False))
            proxy.allow_proxy = bool(desc.get("allow_proxy", True))
            proxy.set_status(desc)
            new_models.append(proxy)

        self.models = new_models

    async def send_command(self, suid: str, cmd: str) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": cmd, "suid": suid}))
        except Exception as exc:
            logger.debug("Failed to send command to remote manager [%d]: %r", self.remote_index, exc)

    async def request_health(self, suid: str) -> dict | None:
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
                "suid": suid,
                "request_id": request_id,
            }))
            return await asyncio.wait_for(future, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    async def request_props(self, suid: str) -> dict | None:
        """Send a get_props request and await the response, returning None on timeout or error."""
        if self._ws is None:
            return None
        self._request_counter += 1
        request_id = str(self._request_counter)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "type": "get_props",
                "suid": suid,
                "request_id": request_id,
            }))
            return await asyncio.wait_for(future, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    async def request_log(
        self, suid: str, before_id: str | None = None, limit: int = 200
    ) -> tuple[list[dict], bool] | None:
        """Request a page of log records from the uplink.

        Returns (lines, has_more) or None if the connection is unavailable.
        """
        if self._ws is None:
            return None
        self._request_counter += 1
        request_id = str(self._request_counter)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            await self._ws.send(json.dumps({
                "type": "get_log",
                "suid": suid,
                "before_id": before_id,
                "limit": limit,
                "request_id": request_id,
            }))
            result = await asyncio.wait_for(future, timeout=10.0)
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            logger.debug("Failed to receive get_log response: %r", e)
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    async def request_slots(self, suid: str) -> list[dict]:
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
                "suid": suid,
                "request_id": request_id,
            }))
            result = await asyncio.wait_for(future, timeout=5.0)
            return result
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            logger.debug("Failed to receive get_slots response: %r", e)
            return []
        finally:
            self._pending_requests.pop(request_id, None)
