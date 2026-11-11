from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from llama_manager import gguf
from llama_manager.config import AppConfig, ModelAdvanced, ModelConfig, save_config
from llama_manager.library import MISS_RESCAN_SECONDS, LibraryEntry, ModelLibrary
from llama_manager.dev import DevViteService
from llama_manager.kv_cache import PrunedFile, prune_slot_storage, resolve_slot_save_path
from llama_manager.protocol.backend import Backend, LlamaManagerProtocol
from llama_manager.proxy import ProxyServer, SlotStatusService
from llama_manager.util.event_bus import EventBus
from llama_manager.util.system import MemorySnapshot, available_memory, total_ram_bytes
from llama_manager.manager.remote_client import RemoteManagerClient
from llama_manager.manager.backends import (
    LocalManagedModel,
    OrphanProcessManager,
    RemoteModelProxy,
    RemoteUnmanagedModel,
)

from .llama_client import LlamaClient

logger = logging.getLogger(__name__)

type LocalModelIdentifier = str

# How long a local model is deprioritized in routing after entering the error
# state (e.g. crash, OOM). Keyed per model suid, not per manager. Penalized
# backends are still tried as a last resort so JIT can restart them; this only
# lets healthy alternatives serving the same model id go first. Cleared early
# when the model reaches running or stopped.
BACKEND_PENALTY_SECONDS = 60

# Share of system RAM autoload may fill when no budget is configured.
DEFAULT_BUDGET_FRACTION = 0.75
# Compute buffers and runtime state on top of weights and KV cache.
SERVER_OVERHEAD_BYTES = 1024**3


class AutoloadUnavailable(RuntimeError):
    """An autoloaded model can't be started (e.g. not enough memory)."""


class LlamaManager(LlamaManagerProtocol):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.event_bus = EventBus()
        self._local_models: dict[LocalModelIdentifier, LocalManagedModel] = {}
        self._remote_unmanaged: dict[LocalModelIdentifier, RemoteUnmanagedModel] = {}
        self.remote_manager_clients: list[RemoteManagerClient] = []
        self.uplink_client_count: int = 0
        self.slot_status = SlotStatusService(self, self.event_bus)
        self.proxy = ProxyServer(self)
        self._data_publisher_task: asyncio.Task | None = None
        self._ttl_task: asyncio.Task | None = None
        self._model_last_activity: dict[str, float] = {}
        self._penalized_until: dict[str, float] = {}
        self.orphans = OrphanProcessManager(Path(config.pid_file))
        # Autoload: servers created on demand for files in the model library.
        self.library = ModelLibrary()
        self._autoload_configs: dict[str, ModelConfig] = {}  # suid -> generated config
        self._autoload_by_quant: dict[str, str] = {}  # library quant id -> suid
        self._autoload_lock = asyncio.Lock()
        self._library_task: asyncio.Task | None = None
        self._header_cache: dict[tuple[str, int, float], dict | None] = {}
        self._pool_total: int | None = None  # bytes in the measured memory pool

    def enable_save_logs(self) -> None:
        self.proxy.enable_save_logs()

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_local_models(self) -> dict[LocalModelIdentifier, LocalManagedModel]:
        return self._local_models

    def get_remote_models(self) -> list[RemoteModelProxy]:
        return [model for client in self.remote_manager_clients for model in client.models]

    def get_remote_unmanaged(self) -> dict[LocalModelIdentifier, RemoteUnmanagedModel]:
        return self._remote_unmanaged

    def get_manager_id(self) -> str:
        return self.config.manager_id

    def get_client(self, model_suid: str) -> LlamaClient | None:
        model: Backend | None = self._local_models.get(model_suid)
        if model is None:
            model = self._remote_unmanaged.get(model_suid)
        
        if model is None:
            return None

        return LlamaClient(model.get_base_url())

    def get_client_at(self, base_url: str) -> LlamaClient:
        return LlamaClient(base_url)

    # ------------------------------------------------------------------
    # Backend penalty tracking
    # ------------------------------------------------------------------

    def penalize(self, suid: str) -> None:
        """Suppress a backend from routing for BACKEND_PENALTY_SECONDS."""
        deadline = time.monotonic() + BACKEND_PENALTY_SECONDS
        self._penalized_until[suid] = deadline
        logger.info("Backend %s penalized for %ds", suid, BACKEND_PENALTY_SECONDS)

    def clear_penalty(self, suid: str) -> None:
        self._penalized_until.pop(suid, None)

    def is_penalized(self, suid: str) -> bool:
        deadline = self._penalized_until.get(suid)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            self._penalized_until.pop(suid, None)
            return False
        return True

    def notify_state_change(self, suid: str, state: str) -> None:
        """Called by backends when their state changes."""
        if state in ("running", "stopped"):
            self.clear_penalty(suid)
        elif state == "error":
            self.penalize(suid)

        # Start the TTL idle timer fresh on every start (manual, auto, or JIT);
        # otherwise a stale timestamp from a previous run stops it immediately.
        if state == "running":
            self.touch(suid)

        if suid in self._autoload_configs:
            self.event_bus.publish({"type": "autoloaded"})  # loaded/unloaded: refresh the list

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    def find_backends(self, model_id: str | None) -> list[Backend]:
        """Return all backends matching model_id, best first.

        Remote proxies (uplink) come first, then local/unmanaged sorted by
        priority descending. Penalized backends are kept as a last resort
        (after all healthy ones) so a crashed model can still be JIT-restarted
        when nothing else serves it.
        """
        result: list[Backend] = []

        # Remote proxies — not subject to local penalty tracking
        for proxy in self.get_remote_models():
            if model_id is None or model_id in proxy.get_model_ids():
                result.append(proxy)

        def _rank(b: Backend) -> tuple[bool, int]:
            m = self.get_model_config(b.get_suid())
            priority = m.priority if m is not None else 1
            return (not self.is_penalized(b.get_suid()), priority)

        candidates: list[Backend] = []
        for m in self._local_models.values():
            if model_id is None or model_id in m.get_model_ids():
                candidates.append(m)
        for m in self._remote_unmanaged.values():
            if model_id is None or model_id in m.get_model_ids():
                candidates.append(m)

        candidates.sort(key=_rank, reverse=True)
        result.extend(candidates)
        return result

    def find_backend(self, model_id: str | None) -> Backend | None:
        """Return the best backend for model_id (penalized last), or None."""
        backends = self.find_backends(model_id)
        return backends[0] if backends else None

    def find_backend_by_suid(self, suid: str) -> Backend | None:
        """Find the backend by suid."""
        return (self._local_models.get(suid)
                or self._remote_unmanaged.get(suid)
                or next((proxy for proxy in self.get_remote_models() if proxy.get_suid() == suid), None))

    def get_model_config(self, suid: str) -> ModelConfig | None:
        """Return the ModelConfig for a configured or autoloaded model by suid."""
        for m in self.config.models:
            if m.suid == suid:
                return m
        return self._autoload_configs.get(suid)

    def is_autoloaded(self, suid: str) -> bool:
        return suid in self._autoload_configs

    def autoloaded_servers(self) -> list[dict]:
        """Autoloaded servers this session (loaded or not), for the UI."""
        now = time.monotonic()
        servers = []
        for suid, cfg in self._autoload_configs.items():
            local_model = self._local_models.get(suid)
            if local_model is None:
                continue
            last = self._model_last_activity.get(suid)
            estimate = self.estimate_memory(cfg)
            servers.append({
                "suid": suid,
                "name": cfg.name,
                "model_id": cfg.effective_id,
                "model_path": cfg.model_path,
                "ctx_size": cfg.ctx_size,
                "parallel": cfg.parallel,
                "idle_ttl": cfg.model_ttl,
                "memory_mib": estimate // 1024**2 if estimate else None,
                "idle_seconds": round(now - last) if last is not None else None,
                **local_model.get_status(),
            })
        return servers

    # ------------------------------------------------------------------
    # Autoload: start servers for library models named in requests
    # ------------------------------------------------------------------

    def autoload_folders(self) -> list[Path]:
        folders = [f for f in self.config.autoload.folders if f.strip()]
        if not folders and self.config.web_ui.default_model_path:
            folders = [self.config.web_ui.default_model_path]
        return [Path(f).expanduser() for f in folders]

    def memory_budget_bytes(self) -> int | None:
        """Memory to size models against when choosing a repo's default quant.

        A fixed budget if configured; otherwise the measured pool (VRAM, or
        the Metal limit on Apple silicon) minus the reserve, so the choice
        doesn't flip with whatever happens to be loaded right now.
        """
        auto = self.config.autoload
        if auto.memory_budget_gib is not None:
            return int(auto.memory_budget_gib * 1024**3)
        if self._pool_total:
            return max(0, self._pool_total - int(auto.memory_reserve_gib * 1024**3))
        return self._fallback_budget()

    @staticmethod
    def _fallback_budget() -> int | None:
        total = total_ram_bytes()
        return int(total * DEFAULT_BUDGET_FRACTION) if total else None

    def refresh_library(self) -> None:
        """Rescan model folders in the background."""
        if not self.config.autoload.enabled:
            return
        if self._library_task is not None and not self._library_task.done():
            return
        async def refresh() -> None:
            await self.measure_memory()  # sizes the pool for default-quant choice
            await self.library.refresh(self.autoload_folders())

        self._library_task = asyncio.create_task(refresh())

    def library_model_ids(self) -> list[str]:
        return self.library.model_ids() if self.config.autoload.enabled else []

    async def resolve_backends(self, model_id: str | None) -> list[Backend]:
        """Backends for a request: configured ones first, else an autoloaded one.

        Raises library.AmbiguousModel when a bare repo name matches several.
        """
        backends = self.find_backends(model_id)
        if backends or model_id is None or not self.config.autoload.enabled:
            return backends
        budget, pins = self.memory_budget_bytes(), self.config.autoload.pins
        entry = self.library.resolve(model_id, budget, pins)
        if entry is None:
            # Maybe a file that appeared since the last scan.
            await self.library.refresh(self.autoload_folders(), if_older_than=MISS_RESCAN_SECONDS)
            entry = self.library.resolve(model_id, budget, pins)
        if entry is None:
            return []
        return [self._autoload_backend(entry)]

    def _autoload_backend(self, entry: LibraryEntry) -> LocalManagedModel:
        suid = self._autoload_by_quant.get(entry.quant_id)
        if suid is not None and suid in self._local_models:
            return self._local_models[suid]
        model_config = self._autoload_model_config(entry)
        local_model = self._build_local_model(model_config, _free_port(), None, self.config)
        self._autoload_configs[model_config.suid] = model_config
        self._autoload_by_quant[entry.quant_id] = model_config.suid
        self._local_models[model_config.suid] = local_model
        self.event_bus.publish({"type": "autoloaded"})
        return local_model

    def _autoload_model_config(self, entry: LibraryEntry) -> ModelConfig:
        """Settings for a library model, from its header and the autoload defaults."""
        info, auto = entry.info, self.config.autoload
        trained = info.get("context_length")
        adv = ModelAdvanced(**info.get("sampling", {}))
        embd = info.get("embedding_length")
        for proj in gguf.find_projectors(entry.path):
            dims = [d for d in (proj["projector"] or {}).values() if d]
            if embd is None or embd in dims:
                adv.mmproj_path = proj["path"]
                break
        if info.get("embedding"):
            adv.extra_args = ["--embedding"]
        return ModelConfig(
            suid=str(uuid.uuid5(uuid.NAMESPACE_URL, f"llama-manager-autoload:{entry.quant_id}")),
            name=entry.quant_id,
            id=entry.quant_id,
            model_path=str(entry.path),
            ctx_size=min(auto.ctx_size, trained) if trained else auto.ctx_size,
            parallel=auto.parallel,
            model_ttl=auto.idle_ttl,
            advanced=adv,
        )

    def estimate_memory(self, model_config: ModelConfig) -> int | None:
        """Approximate memory a model server needs: weights, projector, KV cache, overhead."""
        try:
            path = gguf.metadata_file(model_config.model_path)
            st = path.stat()
        except (OSError, ValueError):
            return None
        key = (str(path), st.st_size, st.st_mtime)
        if key not in self._header_cache:
            try:
                self._header_cache[key] = gguf.summarize(path)
            except (OSError, gguf.GGUFError):
                self._header_cache[key] = None
        info = self._header_cache[key]
        if info is None:
            return None
        weights = sum(p.stat().st_size for p in gguf.model_files(path) if p.exists())
        proj = Path(model_config.advanced.mmproj_path).expanduser() if model_config.advanced.mmproj_path else None
        if proj is not None and proj.is_file():
            weights += proj.stat().st_size
        per_slot = model_config.ctx_size or info.get("context_length") or 0
        kv = gguf.kv_cache_bytes(info, per_slot, model_config.parallel,
                                 bool(model_config.kv_unified), model_config.advanced.swa_full) or 0
        return weights + kv + SERVER_OVERHEAD_BYTES

    async def _is_busy(self, local_model: LocalManagedModel) -> bool:
        try:
            slots = await local_model.get_slots()
        except Exception:
            return False
        return any(s.get("is_processing") for s in slots or [])

    async def measure_memory(self) -> MemorySnapshot | None:
        server = self.config.api_server.llama_server_path or None
        snap = await asyncio.to_thread(available_memory, server)
        if snap is not None:
            self._pool_total = snap.total
        return snap

    async def _make_room(self, target: LocalManagedModel) -> None:
        """Stop idle autoloaded servers, stalest first, until target fits.

        With a fixed memory budget, fit is judged from estimates. Otherwise it's
        judged from memory measured now (free VRAM on NVIDIA, unified memory on
        Apple silicon), which also accounts for processes this manager doesn't
        run. Busy and configured servers are never stopped.
        """
        target_cfg = self.get_model_config(target.get_suid())
        assert target_cfg is not None
        need = self.estimate_memory(target_cfg) or 0
        max_loaded = max(1, self.config.autoload.max_loaded)
        gib = 1024**3

        def loaded() -> list[LocalManagedModel]:
            return [m for m in self._local_models.values()
                    if m is not target and m.state.value in ("starting", "running")]

        def estimate(m: LocalManagedModel) -> int:
            cfg = self.get_model_config(m.get_suid())
            return (self.estimate_memory(cfg) or 0) if cfg else 0

        async def idle_autoloaded() -> list[LocalManagedModel]:
            """Eviction candidates, stalest (longest since last use) first."""
            candidates = sorted(
                (m for m in loaded() if self.is_autoloaded(m.get_suid())),
                key=lambda m: self._model_last_activity.get(m.get_suid(), 0.0),
            )
            return [m for m in candidates if not await self._is_busy(m)]

        async def evict(victim: LocalManagedModel) -> None:
            self.proxy.log(f"Autoload: stopping idle [{victim.get_name()}] to make room for [{target_cfg.name}]")
            await victim.stop()

        budget = self.memory_budget_bytes() if self.config.autoload.memory_budget_gib is not None else None
        snap = None if budget is not None else await self.measure_memory()
        if budget is None and snap is None:
            budget = self._fallback_budget()  # can't measure here: estimate against 75% of RAM

        if budget is not None:
            used = lambda: sum(estimate(m) for m in loaded())  # noqa: E731
            fixed_use = sum(estimate(m) for m in loaded() if not self.is_autoloaded(m.get_suid()))
            if fixed_use + need > budget:
                where = f" alongside {fixed_use / gib:.1f} GiB of configured servers" if fixed_use else ""
                raise AutoloadUnavailable(
                    f"{target_cfg.name} needs ~{need / gib:.1f} GiB, which doesn't fit the "
                    f"{budget / gib:.0f} GiB memory budget{where}")
            while (sum(1 for m in loaded() if self.is_autoloaded(m.get_suid())) >= max_loaded
                   or used() + need > budget):
                victims = await idle_autoloaded()
                if not victims:
                    raise AutoloadUnavailable(
                        f"not enough memory to load {target_cfg.name}: needs ~{need / gib:.1f} GiB, "
                        f"{used() / gib:.1f} GiB of the {budget / gib:.0f} GiB budget in use by busy servers")
                await evict(victims[0])
            return

        reserve = int(self.config.autoload.memory_reserve_gib * gib)
        while True:
            assert snap is not None
            # Servers still starting haven't allocated yet; count their estimates.
            pending = sum(estimate(m) for m in loaded() if m.state.value == "starting")
            free = snap.available - reserve - pending
            too_many = sum(1 for m in loaded() if self.is_autoloaded(m.get_suid())) >= max_loaded
            if not too_many and need <= free:
                return
            victims = await idle_autoloaded()
            reclaimable = sum(estimate(v) for v in victims)
            if need > free + reclaimable:
                raise AutoloadUnavailable(
                    f"not enough memory to load {target_cfg.name}: needs ~{need / gib:.1f} GiB, "
                    f"{max(free, 0) / gib:.1f} GiB free on {snap.source} after a {reserve / gib:.0f} GiB reserve"
                    + (f", and stopping idle autoloaded servers would free only ~{reclaimable / gib:.1f} GiB"
                       if victims else ""))
            if not victims:
                raise AutoloadUnavailable(
                    f"{max_loaded} autoloaded server(s) already running and all are busy")
            victim = victims[0]
            before = snap.available
            await evict(victim)
            snap = await self._await_release(before, estimate(victim))

    async def _await_release(self, before: int, expected: int) -> MemorySnapshot:
        """Re-measure after a stop until the freed memory shows up (or 10 s pass)."""
        deadline = time.monotonic() + 10
        while True:
            snap = await self.measure_memory()
            if snap is None:
                raise AutoloadUnavailable("memory can no longer be measured")
            if snap.available - before >= expected // 2 or time.monotonic() >= deadline:
                return snap
            await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # JIT + TTL: server lifecycle APIs used by the proxy handler
    # ------------------------------------------------------------------

    def touch(self, suid: str) -> None:
        """Record activity for a model, resetting its TTL timer."""
        self._model_last_activity[suid] = time.monotonic()

    def get_slot_save_path(self, suid: str) -> Path | None:
        """Return the KV cache slot save directory for a model, or None."""
        m = self.get_model_config(suid)
        if m is None:
            return None
        adv = m.advanced
        if not adv.kv_cache:
            return None
        if adv.slot_save_path:
            return Path(adv.slot_save_path).expanduser().resolve()
        base = self.config.web_ui.slot_save_path or "./slot_saves"
        model_id = m.effective_id or suid
        return Path(base).expanduser().resolve() / model_id

    def prune_slot_storage(self, keep: Path) -> list[PrunedFile]:
        """Enforce web_ui.slot_storage_limit across all models' slot save dirs.

        Call after saving ``keep``; returns the files deleted to make room.
        """
        limit_gb = self.config.web_ui.slot_storage_limit
        if limit_gb is None:
            return []
        dirs = {p for m in self.config.models if (p := self.get_slot_save_path(m.suid)) is not None}
        return prune_slot_storage(dirs, limit_gb * 1024**3, keep)

    async def ensure_server(self, backend: Backend) -> None:
        """Ensure a backend is ready to serve requests.

        Delegates to the backend's own ensure_ready implementation so each
        backend type can handle its own startup and readiness semantics.
        """
        jit_enabled = self.config.api_server.jit_model_server
        timeout = self.config.api_server.jit_timeout or 80
        if isinstance(backend, LocalManagedModel) and self.is_autoloaded(backend.get_suid()):
            if backend.state.value in ("stopped", "error"):
                # One load at a time, held until the server is up: the next
                # load then measures memory after this one has allocated.
                async with self._autoload_lock:
                    if backend.state.value in ("stopped", "error"):
                        await self._make_room(backend)
                        self.proxy.log(f"Autoload: starting [{backend.get_name()}]")
                        await backend.start()
                        await backend.ensure_ready(True, timeout)
                        return
            jit_enabled = True  # autoloaded servers always start on demand
        await backend.ensure_ready(jit_enabled, timeout)

    async def task_ttl_checker(self) -> None:
        """Background task that stops idle models whose TTL has expired."""
        while True:
            await asyncio.sleep(30)
            try:
                now = time.monotonic()
                for m in [*self.config.models, *self._autoload_configs.values()]:
                    if m.model_ttl is None or m.type == "remote":
                        continue
                    local_model = self._local_models.get(m.suid)
                    if local_model is None or local_model.state.value != "running":
                        continue
                    last = self._model_last_activity.get(m.suid)
                    if last is None:
                        continue
                    if now - last > m.model_ttl * 60:
                        name = m.name or m.suid
                        self.proxy.log(f"TTL expired for [{name}], stopping server")
                        await local_model.stop()
            except Exception:
                pass  # don't crash the background task

    # ------------------------------------------------------------------
    # Model construction helpers
    # ------------------------------------------------------------------

    def _make_local_model(self, idx: int, config: AppConfig) -> LocalManagedModel:
        """Construct a LocalManagedModel with all config pre-resolved."""
        port = config.api_server.llama_server_starting_port + idx
        return self._build_local_model(config.models[idx], port, resolve_slot_save_path(config, idx), config)

    def _build_local_model(
        self, model_config: ModelConfig, port: int, slot_save_path: Path | None, config: AppConfig,
    ) -> LocalManagedModel:
        raw_path = model_config.advanced.llama_server_path or config.api_server.llama_server_path
        llama_server_path = Path(raw_path).expanduser() if raw_path else None
        return LocalManagedModel(
            manager=self,
            model_config=model_config,
            port=port,
            event_bus=self.event_bus,
            log_buffer_size=config.web_ui.log_buffer_size,
            llama_server_path=llama_server_path,
            slot_save_path=slot_save_path,
            orphans=self.orphans,
            filter_slot_queries=config.web_ui.filter_slot_queries,
        )

    def _update_local_model(self, local_model: LocalManagedModel, idx: int, config: AppConfig) -> None:
        """Push refreshed config to an existing LocalManagedModel."""
        model_config = config.models[idx]
        raw_path = model_config.advanced.llama_server_path or config.api_server.llama_server_path
        llama_server_path = Path(raw_path).expanduser() if raw_path else None
        slot_save_path = resolve_slot_save_path(config, idx)
        local_model.update_config(model_config, llama_server_path, slot_save_path, config.web_ui.filter_slot_queries)

    # ------------------------------------------------------------------
    # Model initialisation
    # ------------------------------------------------------------------

    def _initialize_models(self, config: AppConfig) -> None:
        """Build _local_models and _remote_unmanaged fresh from config."""
        local_models: dict[LocalModelIdentifier, LocalManagedModel] = {}
        unmanaged: dict[LocalModelIdentifier, RemoteUnmanagedModel] = {}
        for idx, model_config in enumerate(config.models):
            key = model_config.suid
            if model_config.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(model_config, self)
            else:
                local_models[key] = self._make_local_model(idx, config)
        self._local_models = local_models
        self._remote_unmanaged = unmanaged

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _start(self, vite: DevViteService | None) -> None:
        self._initialize_models(self.config)
        self.remote_manager_clients = []

        # Free ports held by llama-servers left behind by a previous manager
        # that was killed before it could shut them down.
        await self.orphans.kill_orphans()

        if vite is not None:
            await vite.start()

        await self.proxy.start()

        for model_config in self.config.models:
            local_model = self._local_models.get(model_config.suid)
            if model_config.auto_start and local_model is not None:
                logger.info("Auto-starting model %s", model_config.name or model_config.suid)
                await local_model.start()

        # _sync_remote_managers works for initial startup (empty list) and
        # incremental apply_config updates — no separate connect path needed.
        await self._sync_remote_managers(self.config)

        self._data_publisher_task = asyncio.create_task(self.data_publisher())
        self._ttl_task = asyncio.create_task(self.task_ttl_checker())
        await self.slot_status.start()
        self.refresh_library()

    async def _stop(self, vite: DevViteService | None) -> None:
        # Kill Vite first — before any slow model/proxy teardown.  In dev mode
        # watchfiles sends SIGINT then waits sigint_timeout before SIGKILL;
        # if model cleanup exceeds that window the subprocess is SIGKILLed and
        # Python's atexit handlers never run, leaving Vite orphaned.
        if vite is not None:
            await vite.stop()

        if self._data_publisher_task is not None:
            self._data_publisher_task.cancel()
            self._data_publisher_task = None

        if self._ttl_task is not None:
            self._ttl_task.cancel()
            self._ttl_task = None

        await self.slot_status.stop()

        for client in self.remote_manager_clients:
            await client.stop()

        await self.proxy.stop()

        # Stop concurrently: large models can take seconds each to exit, and
        # the dev reloader only allows a limited window before SIGKILL.
        await asyncio.gather(*(m.stop() for m in self._local_models.values()))

    def get_lifespan(self, vite: DevViteService | None = None):
        @asynccontextmanager
        async def lifespan(_app: FastAPI):
            await self._start(vite)
            try:
                yield
            finally:
                await self._stop(vite)

        return lifespan

    # ------------------------------------------------------------------
    # Config application (incremental update)
    # ------------------------------------------------------------------

    async def apply_config(self, config: AppConfig) -> AppConfig:
        """Save config and incrementally sync local models and remote clients."""
        if config.manager_uplink.enabled and not config.manager_uplink.token:
            config.manager_uplink.token = self.generate_token()

        save_config(config)
        self.config = config
        self.event_bus.publish({"type": "config_changed"})

        local_models = self._local_models
        unmanaged = self._remote_unmanaged

        # Sync all configured models (handles new additions, type changes, config updates)
        for idx, model_config in enumerate(config.models):
            key = model_config.suid
            if model_config.type == "remote":
                unmanaged[key] = RemoteUnmanagedModel(model_config, self)
                existing = local_models.get(key)
                if existing is not None and existing.state.value == "stopped":
                    del local_models[key]
            else:
                unmanaged.pop(key, None)
                if key in local_models:
                    self._update_local_model(local_models[key], idx, config)
                else:
                    local_models[key] = self._make_local_model(idx, config)

        # Shrink: stop and remove entries for models no longer in config
        # (autoloaded servers aren't in config; their TTL stops them).
        valid_suids = {m.suid for m in config.models} | self._autoload_configs.keys()
        orphaned_keys = [k for k in local_models if k not in valid_suids]
        for key in orphaned_keys:
            local_model = local_models[key]
            if local_model.state.value not in ("stopped", "error"):
                await local_model.stop()
            del local_models[key]
            unmanaged.pop(key, None)

        await self._sync_remote_managers(config)
        self.refresh_library()
        return config

    # ------------------------------------------------------------------
    # Remote manager client sync
    # ------------------------------------------------------------------

    async def _sync_remote_managers(self, config: AppConfig) -> None:
        clients = self.remote_manager_clients
        new_clients: list[RemoteManagerClient] = []
        for i, remote_config in enumerate(config.remote_managers):
            if i < len(clients):
                existing = clients[i]
                existing_config = existing.get_config()
                if (existing_config.host != remote_config.host
                        or existing_config.port != remote_config.port
                        or existing_config.token != remote_config.token):
                    await existing.stop()
                    if remote_config.enabled and remote_config.host:
                        client = RemoteManagerClient(i, remote_config, config, self.event_bus)
                        await client.start()
                        new_clients.append(client)
                    else:
                        new_clients.append(existing)
                else:
                    existing.set_config(remote_config)
                    new_clients.append(existing)
            else:
                if remote_config.enabled and remote_config.host:
                    client = RemoteManagerClient(i, remote_config, config, self.event_bus)
                    await client.start()
                    new_clients.append(client)

        for i in range(len(config.remote_managers), len(clients)):
            await clients[i].stop()

        self.remote_manager_clients = new_clients

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def data_publisher(self) -> None:
        """Publish health events for running local models and remote unmanaged models."""
        while True:
            try:
                for local_model in self._local_models.values():
                    if local_model.state.value != "running":
                        continue
                    try:
                        health = await LlamaClient(local_model.get_base_url()).get_health()
                        if health is not None:
                            self.event_bus.publish({
                                "type": "health",
                                "id": local_model.get_suid(),
                                "data": {"health": health},
                            })
                    except Exception:
                        logger.warning("data_publisher: health fetch failed for %s", local_model.get_suid(), exc_info=True)

                for unmanaged in self._remote_unmanaged.values():
                    try:
                        # Always publish (including "offline") so the UI never goes stale.
                        health = await unmanaged.get_health()
                        self.event_bus.publish({
                            "type": "health",
                            "id": unmanaged.get_suid(),
                            "data": {"health": health},
                        })
                    except Exception:
                        logger.warning("data_publisher: health fetch failed for %s", unmanaged.get_suid(), exc_info=True)
            except Exception:
                logger.warning("data_publisher: unexpected error", exc_info=True)

            await asyncio.sleep(3.0)

    def generate_token(self) -> str:
        return secrets.token_hex(32)


def _free_port() -> int:
    """A currently unused localhost port for an autoloaded llama-server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
