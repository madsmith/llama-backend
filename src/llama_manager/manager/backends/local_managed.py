from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time
from enum import Enum
from pathlib import Path

from llama_manager.config import ModelConfig
from llama_manager.event_bus import EventBus
from llama_manager.llama_client import LlamaClient
from llama_manager.log_buffer import LogBuffer
from llama_manager.protocol.backend import ManagedBackend

# Regexes for parsing prompt processing progress from llama-server logs
_RE_NEW_PROMPT = re.compile(
    r"slot update_slots: id\s+(\d+) \|.*\| new prompt,.*n_tokens\s*=\s*(\d+)"
)
_RE_PROMPT_PROGRESS = re.compile(
    r"slot update_slots: id\s+(\d+) \|.*\| prompt processing progress,"
    r"\s*(?:n_tokens|n_past)\s*=\s*(\d+).*progress\s*=\s*([\d.]+)"
)
_RE_PROMPT_DONE = re.compile(r"slot update_slots: id\s+(\d+) \|.*\| prompt done")

log = logging.getLogger(__name__)


class ServerState(str, Enum):
    unknown = "unknown"
    stopped = "stopped"
    starting = "starting"
    running = "running"
    stopping = "stopping"
    error = "error"


class LocalManagedModel(ManagedBackend):
    def __init__(
        self,
        server_id: str,
        model_config: ModelConfig,
        port: int,
        event_bus: EventBus,
        log_buffer_size: int,
        llama_server_path: Path | None,
        slot_save_path: Path | None,
    ) -> None:
        self._server_id = server_id
        self._model_config = model_config
        self._llama_server_path = llama_server_path
        self._slot_save_path = slot_save_path
        self.event_bus = event_bus

        self.state: ServerState = ServerState.stopped
        self.process: asyncio.subprocess.Process | None = None
        self.pid: int | None = None
        self.host: str = "127.0.0.1"
        self.port: int = port
        self.started_at: float | None = None
        self.log_buffer = LogBuffer(maxlen=log_buffer_size)
        self._subscribers: list[asyncio.Queue[dict]] = []
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.prompt_progress: dict[int, dict] = {}  # slot_id -> progress info

    def update_config(
        self,
        model_config: ModelConfig,
        llama_server_path: Path | None,
        slot_save_path: Path | None,
    ) -> None:
        """Push updated config to this model. Takes effect on next start."""
        self._model_config = model_config
        self._llama_server_path = llama_server_path
        self._slot_save_path = slot_save_path

    # ------------------------------------------------------------------
    # Backend protocol
    # ------------------------------------------------------------------

    def get_suid(self) -> str:
        return self._server_id

    def get_name(self) -> str | None:
        return self._model_config.name

    def get_base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def get_model_ids(self) -> list[str]:
        return [self._model_config.effective_id]

    def is_available(self) -> bool:
        return self.state == ServerState.running

    async def get_slots(self) -> list[dict] | None:
        return await LlamaClient(self.get_base_url()).get_slots()

    async def get_health(self) -> dict:
        return await LlamaClient(self.get_base_url()).get_health() or {"status": "unknown"}

    # ------------------------------------------------------------------
    # ManagedBackend protocol
    # ------------------------------------------------------------------

    def get_log_buffer(self) -> LogBuffer:
        return self.log_buffer

    def get_server_identifier(self) -> str:
        return self._server_id

    def _log(self, text: str) -> None:
        line = self.log_buffer.append(text)
        self.event_bus.publish({
            "type": "server_log",
            "id": self._server_id,
            "data": {"line_id": line.id, "text": line.text},
        })
        log.debug("[local_managed_model] %s", text)

    def _set_state(self, new: ServerState) -> None:
        old = self.state
        self.state = new
        log.debug("state %s -> %s", old.value, new.value)
        self.event_bus.publish({
            "type": "server_status",
            "id": self._server_id,
            "data": {"state": new.value},
        })

    async def start(self) -> None:
        async with self._lock:
            log.debug("start() called, current state=%s", self.state.value)
            if self.state in (ServerState.running, ServerState.starting):
                log.debug("already %s, ignoring start", self.state.value)
                return

            self.log_buffer.clear()
            self._set_state(ServerState.starting)

            if not self._llama_server_path:
                self._fail("llama-server path not configured — set it in Settings")
                return
            if not self._llama_server_path.exists():
                self._fail(f"llama-server not found: {self._llama_server_path}")
                return

            try:
                model_path = self._resolve_model_path(self._model_config)
            except FileNotFoundError as exc:
                self._fail(str(exc))
                return

            cmd = self._build_command(
                self._llama_server_path,
                model_path,
                self.host,
                self.port,
                self._model_config,
                self._slot_save_path,
            )
            await self._spawn(cmd)

    @staticmethod
    def _resolve_model_path(model_config: ModelConfig) -> Path:
        if not model_config.model_path:
            raise FileNotFoundError(
                "model path not configured \u2014 set it in Settings"
            )
        path = Path(model_config.model_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"model not found: {path}")
        return path

    @staticmethod
    def _build_command(
        server_path: Path,
        model_path: Path,
        host: str,
        port: int,
        model_config: ModelConfig,
        slot_save_path: Path | None,
    ) -> list[str]:
        adv = model_config.advanced
        cmd = [
            str(server_path),
            "--model", str(model_path),
            "--host", host,
            "--port", str(port),
            "--ctx-size", str(model_config.ctx_size * model_config.parallel),
            "--n-gpu-layers", str(model_config.n_gpu_layers),
            "--parallel", str(model_config.parallel),
        ]

        if adv.slot_prompt_similarity is not None:
            cmd += ["--slot-prompt-similarity", str(adv.slot_prompt_similarity)]

        if adv.repeat_penalty is not None:
            cmd += ["--repeat-penalty", str(adv.repeat_penalty)]

        if adv.repeat_last_n is not None:
            cmd += ["--repeat-last-n", str(adv.repeat_last_n)]

        if slot_save_path is not None or adv.slot_save_path:
            cmd += ["--slots"]

        if slot_save_path is not None:
            slot_save_path.mkdir(parents=True, exist_ok=True)
            cmd += ["--slot-save-path", str(slot_save_path)]

        if adv.swa_full:
            cmd += ["--swa-full"]

        if adv.max_prediction_tokens is not None:
            cmd += ["--n-predict", str(adv.max_prediction_tokens)]

        cmd += adv.extra_args
        return cmd

    async def _spawn(self, cmd: list[str]) -> None:
        self._log(f"$ {shlex.join(cmd)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            self._fail(f"failed to spawn: {type(exc).__name__}: {exc}")
            return

        self.pid = self.process.pid
        self.started_at = time.time()
        self._log(f"spawned pid {self.pid}")
        self._reader_task = asyncio.create_task(self._read_output())

    def _fail(self, msg: str) -> None:
        self.state = ServerState.error
        self._log(f"ERROR: {msg}")
        self.event_bus.publish({
            "type": "server_status",
            "id": self._server_id,
            "data": {"state": self.state.value},
        })

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_internal()

    async def _stop_internal(self) -> None:
        if self.process is None or self.state in (ServerState.stopped, ServerState.unknown):
            return
        self._set_state(ServerState.stopping)

        pid = self.pid
        self._log(f"sending SIGTERM to pid {pid}")
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
            self._log(f"pid {pid} exited (rc={self.process.returncode})")
        except asyncio.TimeoutError:
            self._log(f"pid {pid} did not exit after 10s, sending SIGKILL")
            self.process.kill()
            await self.process.wait()
            self._log(f"pid {pid} killed (rc={self.process.returncode})")

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        self.process = None
        self.pid = None
        self.started_at = None
        self.prompt_progress.clear()
        self._set_state(ServerState.stopped)

    async def restart(self) -> None:
        async with self._lock:
            await self._stop_internal()
        await self.start()

    def get_status(self) -> dict:
        is_running = self.state == ServerState.running
        uptime = (time.time() - self.started_at) if is_running and self.started_at is not None else None
        return {
            "state": self.state.value,
            "pid": self.pid if is_running else None,
            "host": self.host if is_running else None,
            "port": self.port if is_running else None,
            "uptime": uptime,
        }

    def get_prompt_progress(self) -> dict[int, dict]:
        return dict(self.prompt_progress)

    async def _read_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            async for raw in self.process.stdout:
                text = raw.decode("utf-8", errors="replace").rstrip("\n")
                self._log(text)

                m = _RE_NEW_PROMPT.search(text)
                if m:
                    slot_id = int(m.group(1))
                    self.prompt_progress[slot_id] = {
                        "n_total": int(m.group(2)),
                        "n_processed": 0,
                        "progress": 0.0,
                    }
                else:
                    m = _RE_PROMPT_PROGRESS.search(text)
                    if m:
                        slot_id = int(m.group(1))
                        self.prompt_progress[slot_id] = {
                            "n_total": self.prompt_progress.get(slot_id, {}).get("n_total", 0),
                            "n_processed": int(m.group(2)),
                            "progress": float(m.group(3)),
                        }
                    else:
                        m = _RE_PROMPT_DONE.search(text)
                        if m:
                            self.prompt_progress.pop(int(m.group(1)), None)

                if self.state == ServerState.starting and (
                    "listening" in text.lower() or "server listening" in text.lower()
                ):
                    self._set_state(ServerState.running)
        except asyncio.CancelledError:
            return

        # Process exited on its own
        rc = self.process.returncode
        if self.state in (ServerState.starting, ServerState.running):
            self._log(f"process exited unexpectedly (rc={rc})")
            self.state = ServerState.error if rc != 0 else ServerState.stopped
            self.event_bus.publish({
                "type": "server_status",
                "id": self._server_id,
                "data": {"state": self.state.value},
            })
            self.process = None
            self.pid = None
            self.started_at = None
