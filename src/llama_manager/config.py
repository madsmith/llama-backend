from __future__ import annotations

import json
import shutil
import socket
import uuid
from pathlib import Path

from pydantic import BaseModel

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "server_config.json"


def _find_llama_server() -> str:
    """Try to find llama-server on PATH."""
    found = shutil.which("llama-server")
    return found or ""


class ModelAdvanced(BaseModel):
    llama_server_path: str = ""
    stream: bool = True
    supports_developer_role: bool = False
    slot_prompt_similarity: float | None = None
    repeat_penalty: float | None = None
    repeat_last_n: int | None = None
    kv_cache: bool = False
    slot_save_path: str = ""
    swa_full: bool = False
    max_prediction_tokens: int | None = None
    extra_args: list[str] = []


class ModelConfig(BaseModel):
    type: str = "local"
    name: str | None = None
    id: str | None = None
    model_path: str = ""
    ctx_size: int = 65536
    n_gpu_layers: int = -1
    parallel: int = 2
    auto_start: bool = False
    model_ttl: int | None = None
    advanced: ModelAdvanced = ModelAdvanced()
    remote_address: str = ""
    remote_model_id: str | None = None

    @property
    def effective_id(self) -> str:
        """Return explicit id, or derive from model_path/remote info."""
        if self.id:
            return self.id
        if self.type == "remote":
            if self.remote_model_id:
                return self.remote_model_id
            if self.remote_address:
                return (
                    self.remote_address.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[0]
                )
            return ""
        if self.model_path:
            from pathlib import PurePosixPath

            stem = PurePosixPath(self.model_path).stem
            return stem.lower()
        return ""


class WebUIConfig(BaseModel):
    log_buffer_size: int = 10_000
    slot_save_path: str = ""
    poll_server_status: int | None = None
    poll_proxy_status: int | None = None
    poll_health: int | None = None
    poll_slots: int | None = None
    poll_slots_active: int | None = None


class ApiServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 1234
    llama_server_starting_port: int = 3210
    llama_server_path: str = ""
    jit_model_server: bool = True
    jit_timeout: int | None = None


class ManagerUplinkConfig(BaseModel):
    enabled: bool = False
    token: str = ""


class RemoteManagerConfig(BaseModel):
    name: str | None = None
    host: str = ""
    port: int = 8000
    token: str = ""
    reconnect_interval: int = 5
    enabled: bool = True

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws/manager"


class AppConfig(BaseModel):
    models: list[ModelConfig] = [ModelConfig()]
    web_ui: WebUIConfig = WebUIConfig()
    api_server: ApiServerConfig = ApiServerConfig()
    manager_uplink: ManagerUplinkConfig = ManagerUplinkConfig()
    remote_managers: list[RemoteManagerConfig] = []
    manager_id: str = ""


def load_config() -> AppConfig:
    is_missing = not CONFIG_PATH.exists()
    if is_missing:
        cfg = AppConfig()
    else:
        data = json.loads(CONFIG_PATH.read_text())
        cfg = AppConfig(**data)

    if not cfg.api_server.llama_server_path:
        cfg.api_server.llama_server_path = _find_llama_server()

    if not cfg.manager_id:
        hostname = socket.gethostname()
        port = cfg.api_server.port
        cfg.manager_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{hostname}:{port}"))
        save_config(cfg)
    elif is_missing:
        save_config(cfg)

    return cfg


def save_config(cfg: AppConfig) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg.model_dump(), indent=2) + "\n")
