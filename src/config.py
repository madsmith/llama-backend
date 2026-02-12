from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel

CONFIG_PATH = Path(__file__).resolve().parent.parent / "server_config.json"


def _find_llama_server() -> str:
    """Try to find llama-server on PATH."""
    found = shutil.which("llama-server")
    return found or ""


class ServerConfig(BaseModel):
    llama_server_path: str = ""
    model_path: str = ""
    host: str = "127.0.0.1"
    port: int = 8080
    ctx_size: int = 65536
    n_gpu_layers: int = -1
    parallel: int = 2
    # advanced
    stream: bool = True
    slot_prompt_similarity: float | None = None   # --slot-prompt-similarity (-sps), default 0.10
    repeat_penalty: float | None = None           # --repeat-penalty, default 1.0 (disabled)
    repeat_last_n: int | None = None              # --repeat-last-n, default 64
    slot_save_path: str = ""                      # --slot-save-path, disabled by default
    swa_full: bool = False                        # --swa-full, for SWA models (Gemma 2/3)
    extra_args: list[str] = []
    # manager-level settings
    log_buffer_size: int = 10_000


def load_config() -> ServerConfig:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        cfg = ServerConfig(**data)
    else:
        cfg = ServerConfig()

    if not cfg.llama_server_path:
        cfg.llama_server_path = _find_llama_server()

    save_config(cfg)
    return cfg


def save_config(cfg: ServerConfig) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg.model_dump(), indent=2) + "\n")
