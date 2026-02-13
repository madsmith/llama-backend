from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CONFIG_PATH = Path(__file__).resolve().parent.parent / "server_config.json"


def _find_llama_server() -> str:
    """Try to find llama-server on PATH."""
    found = shutil.which("llama-server")
    return found or ""


class ModelAdvanced(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    llama_server_path: str = ""
    stream: bool = True
    supports_developer_role: bool = Field(default=False, alias="supports-developer-role")
    slot_prompt_similarity: float | None = None
    repeat_penalty: float | None = None
    repeat_last_n: int | None = None
    slot_save_path: str = ""
    swa_full: bool = False
    extra_args: list[str] = []


class ModelConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = "local"
    name: str | None = None
    id: str | None = None
    model_path: str = ""
    ctx_size: int = 65536
    n_gpu_layers: int = -1
    parallel: int = 2
    auto_start: bool = Field(default=False, alias="auto-start")
    model_ttl: int | None = Field(default=None, alias="model-ttl")
    advanced: ModelAdvanced = ModelAdvanced()
    remote_address: str = Field(default="", alias="remote-address")
    remote_model_id: str | None = Field(default=None, alias="remote-model-id")


    @property
    def effective_id(self) -> str:
        """Return explicit id, or derive from model_path/remote info."""
        if self.id:
            return self.id
        if self.type == "remote":
            if self.remote_model_id:
                return self.remote_model_id
            if self.remote_address:
                return self.remote_address.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[0]
            return ""
        if self.model_path:
            from pathlib import PurePosixPath
            stem = PurePosixPath(self.model_path).stem
            return stem.lower()
        return ""


class WebUIConfig(BaseModel):
    log_buffer_size: int = 10_000


class ApiServerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    host: str = "0.0.0.0"
    port: int = 1234
    llama_server_starting_port: int = Field(default=3210, alias="llama-server-starting-port")
    llama_server_path: str = Field(default="", alias="llama-server-path")
    jit_model_server: bool = Field(default=True, alias="jit-model-server")
    jit_timeout: int | None = Field(default=None, alias="jit-timeout")


class AppConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    models: list[ModelConfig] = [ModelConfig()]
    web_ui: WebUIConfig = Field(default_factory=WebUIConfig, alias="web-ui")
    api_server: ApiServerConfig = Field(default_factory=ApiServerConfig, alias="api-server")


def load_config() -> AppConfig:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        cfg = AppConfig(**data)
    else:
        cfg = AppConfig()

    if not cfg.api_server.llama_server_path:
        cfg.api_server.llama_server_path = _find_llama_server()

    save_config(cfg)
    return cfg


def save_config(cfg: AppConfig) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg.model_dump(by_alias=True), indent=2) + "\n")
