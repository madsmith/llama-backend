from __future__ import annotations

import httpx

from llama_manager.config import AppConfig
from llama_manager.remote_manager_client import RemoteModelProxy

from .lifecycle import get_process_managers

# ---------------------------------------------------------------------------
# Backend error helpers
# ---------------------------------------------------------------------------

# Transport-level errors when the backend dies or is unreachable
BACKEND_ERRORS = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)


def backend_error_msg(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "Backend server is not reachable"
    return "Backend server disconnected"


# ---------------------------------------------------------------------------
# Model resolution helpers
# ---------------------------------------------------------------------------


def resolve_model_index(model_id: str | None, config: AppConfig) -> int | None:
    """Resolve a model ID to a model index. Returns None if not found."""
    if not model_id:
        return 0
    for i, m in enumerate(config.models):
        if m.effective_id == model_id:
            return i
    # Fall through to federated remote models
    for pm in get_process_managers():
        if isinstance(pm, RemoteModelProxy) and pm.model_id == model_id:
            return pm.local_index
    return None


def resolve_backend(model_id: str | None, config: AppConfig) -> str | None:
    """Resolve a model ID to a backend URL. Returns None if not found."""
    idx = resolve_model_index(model_id, config)
    if idx is None:
        return None
    # Check for a federated remote model first
    pms = get_process_managers()
    if idx < len(pms) and isinstance(pms[idx], RemoteModelProxy):
        return pms[idx].proxy_url or None
    m = config.models[idx]
    if m.type == "remote":
        return m.remote_address.rstrip("/") if m.remote_address else None
    return f"http://127.0.0.1:{config.api_server.llama_server_starting_port + idx}"


def default_backend(config: AppConfig) -> str:
    return f"http://127.0.0.1:{config.api_server.llama_server_starting_port}"


def rewrite_model_field(body: dict, model_id: str | None, config: AppConfig) -> dict:
    """Rewrite the model field when forwarding to a remote backend."""
    idx = resolve_model_index(model_id, config)
    if idx is None:
        return body
    # Federated remote model: forward using its known model_id
    pms = get_process_managers()
    if idx < len(pms) and isinstance(pms[idx], RemoteModelProxy):
        remote_id = pms[idx].model_id
        if remote_id:
            return {**body, "model": remote_id}
        return body
    # Config-defined remote model with an explicit remote_model_id
    m = config.models[idx]
    if m.type == "remote" and m.remote_model_id:
        return {**body, "model": m.remote_model_id}
    return body


def resolve_server_name(model_id: str | None, config: AppConfig) -> str:
    """Map a model ID to a human-readable server name from config."""
    if not model_id:
        m = config.models[0] if config.models else None
        return m.name or m.effective_id if m else "default"
    for m in config.models:
        if m.effective_id == model_id:
            return m.name or m.effective_id
    return model_id
