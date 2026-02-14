from __future__ import annotations

import asyncio
import logging
import re
import shlex
import time
from enum import Enum
from pathlib import Path

from .config import AppConfig, load_config
from .log_buffer import LogBuffer

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
    stopped = "stopped"
    starting = "starting"
    running = "running"
    stopping = "stopping"
    error = "error"


class ProcessManager:
    def __init__(self, model_index: int = 0, config: AppConfig | None = None) -> None:
        self.model_index = model_index
        self.state: ServerState = ServerState.stopped
        self.process: asyncio.subprocess.Process | None = None
        self.pid: int | None = None
        self.host: str | None = None
        self.port: int | None = None
        self.started_at: float | None = None
        cfg = config or load_config()
        self.log_buffer = LogBuffer(maxlen=cfg.web_ui.log_buffer_size)
        self._subscribers: list[asyncio.Queue[dict]] = []
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.prompt_progress: dict[int, dict] = {}  # slot_id -> progress info
        log.debug(
            "ProcessManager[%d] initialized, state=%s", model_index, self.state.value
        )

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        log.debug("subscriber added (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        try:
            self._subscribers.remove(q)
            log.debug("subscriber removed (total=%d)", len(self._subscribers))
        except ValueError:
            pass

    def _log(self, text: str) -> None:
        """Write to log buffer + broadcast to WS clients + debug to stderr."""
        line = self.log_buffer.append(text)
        self._broadcast({"type": "log", "id": line.id, "text": line.text})
        log.debug("[pm] %s", text)

    def _set_state(self, new: ServerState) -> None:
        old = self.state
        self.state = new
        log.debug("state %s -> %s", old.value, new.value)
        self._broadcast({"type": "state", "state": new.value})

    def _broadcast(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                log.debug("subscriber queue full, dropping message")

    async def start(self, config: AppConfig | None = None) -> None:
        async with self._lock:
            log.debug("start() called, current state=%s", self.state.value)
            if self.state in (ServerState.running, ServerState.starting):
                log.debug("already %s, ignoring start", self.state.value)
                return

            cfg = config or load_config()

            model = cfg.models[self.model_index]

            llama_host, llama_port = self._get_server_address(cfg)
            log.debug("loaded config: %s", cfg.model_dump(by_alias=True))

            self.log_buffer.clear()
            self._set_state(ServerState.starting)

            try:
                server_path = self._resolve_llama_server_path(cfg, model)
                model_path = self._resolve_model_path(model)
            except FileNotFoundError as exc:
                self._fail(str(exc))
                return

            log.debug("resolved server_path=%s, model_path=%s", server_path, model_path)
            cmd = self._build_command(
                server_path,
                model_path,
                llama_host,
                llama_port,
                model,
                cfg,
                self.model_index,
            )
            await self._spawn(cmd, llama_host, llama_port)

    def _get_server_address(self, cfg: AppConfig) -> tuple[str, int]:
        host = "127.0.0.1"
        port = cfg.api_server.llama_server_starting_port + self.model_index
        return host, port

    @staticmethod
    def _resolve_llama_server_path(cfg: AppConfig, model) -> Path:
        raw = model.advanced.llama_server_path or cfg.api_server.llama_server_path
        if not raw:
            raise FileNotFoundError(
                "llama-server path not configured - set it in Settings"
            )
        path = Path(raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"llama-server not found: {path}")
        return path

    @staticmethod
    def _resolve_model_path(model) -> Path:
        if not model.model_path:
            raise FileNotFoundError(
                "model path not configured \u2014 set it in Settings"
            )
        path = Path(model.model_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"model not found: {path}")
        return path

    @staticmethod
    def _build_command(
        server_path: Path,
        model_path: Path,
        host: str,
        port: int,
        model,
        cfg: AppConfig,
        model_index: int,
    ) -> list[str]:
        adv = model.advanced
        cmd = [
            str(server_path),
            "--model",
            str(model_path),
            "--host",
            host,
            "--port",
            str(port),
            "--ctx-size",
            str(model.ctx_size * model.parallel),
            "--n-gpu-layers",
            str(model.n_gpu_layers),
            "--parallel",
            str(model.parallel),
        ]
        if adv.slot_prompt_similarity is not None:
            cmd += ["--slot-prompt-similarity", str(adv.slot_prompt_similarity)]
        if adv.repeat_penalty is not None:
            cmd += ["--repeat-penalty", str(adv.repeat_penalty)]
        if adv.repeat_last_n is not None:
            cmd += ["--repeat-last-n", str(adv.repeat_last_n)]
        if adv.kv_cache:
            if adv.slot_save_path:
                slot_dir = Path(adv.slot_save_path).expanduser().resolve()
            else:
                base = cfg.web_ui.slot_save_path or "./slot_saves"
                model_id = (
                    cfg.models[model_index].effective_id or f"model-{model_index}"
                )
                slot_dir = Path(base).expanduser().resolve() / model_id
            slot_dir.mkdir(parents=True, exist_ok=True)
            cmd += ["--slots", "--slot-save-path", str(slot_dir)]
        elif adv.slot_save_path:
            slot_dir = Path(adv.slot_save_path).expanduser()
            slot_dir.mkdir(parents=True, exist_ok=True)
            cmd += ["--slot-save-path", str(slot_dir)]
        if adv.swa_full:
            cmd += ["--swa-full"]
        cmd += adv.extra_args
        return cmd

    async def _spawn(self, cmd: list[str], host: str, port: int) -> None:
        self._log(f"$ {shlex.join(cmd)}")
        try:
            log.debug("calling create_subprocess_exec")
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            log.debug("spawn failed: %r", exc)
            self._fail(f"failed to spawn: {type(exc).__name__}: {exc}")
            return

        self.pid = self.process.pid
        self.host = host
        self.port = port
        self.started_at = time.time()
        self._log(f"spawned pid {self.pid}")
        log.debug("starting _read_output task")
        self._reader_task = asyncio.create_task(self._read_output())

    def _fail(self, msg: str) -> None:
        log.debug("_fail: %s", msg)
        self.state = ServerState.error
        self._log(f"ERROR: {msg}")
        self._broadcast({"type": "state", "state": self.state.value})

    async def stop(self) -> None:
        log.debug("stop() called")
        async with self._lock:
            await self._stop_internal()

    async def _stop_internal(self) -> None:
        log.debug(
            "_stop_internal: state=%s, process=%s", self.state.value, self.process
        )
        if self.process is None or self.state == ServerState.stopped:
            log.debug("nothing to stop")
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
            log.debug("cancelling reader task")
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            log.debug("reader task cancelled")

        self.process = None
        self.pid = None
        self.host = None
        self.port = None
        self.started_at = None
        self.prompt_progress.clear()
        self._set_state(ServerState.stopped)

    async def restart(self) -> None:
        log.debug("restart() called")
        async with self._lock:
            await self._stop_internal()
        await self.start()

    def shutdown_subscribers(self) -> None:
        """Send empty dict sentinel to all subscriber queues so WS handlers exit."""
        log.debug("shutting down %d subscribers", len(self._subscribers))
        for q in list(self._subscribers):
            try:
                q.put_nowait({})
            except asyncio.QueueFull:
                pass

    def get_status(self) -> dict:
        uptime = None
        if self.started_at is not None:
            uptime = time.time() - self.started_at
        return {
            "state": self.state.value,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "uptime": uptime,
        }

    def get_prompt_progress(self) -> dict[int, dict]:
        return dict(self.prompt_progress)

    async def _read_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        log.debug("_read_output started for pid %s", self.pid)
        try:
            async for raw in self.process.stdout:
                text = raw.decode("utf-8", errors="replace").rstrip("\n")
                self._log(text)

                # Parse prompt processing progress
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
                            "n_total": self.prompt_progress.get(slot_id, {}).get(
                                "n_total", 0
                            ),
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
            log.debug("_read_output cancelled")
            return

        # Process exited on its own
        rc = self.process.returncode
        log.debug("_read_output: stdout EOF, rc=%s, state=%s", rc, self.state.value)
        if self.state in (ServerState.starting, ServerState.running):
            self._log(f"process exited unexpectedly (rc={rc})")
            self.state = ServerState.error if rc != 0 else ServerState.stopped
            self._broadcast({"type": "state", "state": self.state.value})
            self.process = None
            self.pid = None
            self.port = None
            self.started_at = None
