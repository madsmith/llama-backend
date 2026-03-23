from __future__ import annotations

import httpx

from ..config import load_config
from ..remote_manager_client import RemoteModelProxy
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


def resolve_model_index(model_id: str | None) -> int | None:
    """Resolve a model ID to a model index. Returns None if not found."""
    if not model_id:
        return 0
    cfg = load_config()
    for i, m in enumerate(cfg.models):
        if m.effective_id == model_id:
            return i
    # Fall through to federated remote models
    for pm in get_process_managers():
        if isinstance(pm, RemoteModelProxy) and pm.model_id == model_id:
            return pm.local_index
    return None


def resolve_backend(model_id: str | None) -> str | None:
    """Resolve a model ID to a backend URL. Returns None if not found."""
    idx = resolve_model_index(model_id)
    if idx is None:
        return None
    # Check for a federated remote model first
    pms = get_process_managers()
    if idx < len(pms) and isinstance(pms[idx], RemoteModelProxy):
        return pms[idx].proxy_url or None
    cfg = load_config()
    m = cfg.models[idx]
    if m.type == "remote":
        return m.remote_address.rstrip("/") if m.remote_address else None
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port + idx}"


def default_backend() -> str:
    cfg = load_config()
    return f"http://127.0.0.1:{cfg.api_server.llama_server_starting_port}"


def rewrite_model_field(body: dict, model_id: str | None) -> dict:
    """Rewrite the model field when forwarding to a remote backend."""
    idx = resolve_model_index(model_id)
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
    cfg = load_config()
    m = cfg.models[idx]
    if m.type == "remote" and m.remote_model_id:
        return {**body, "model": m.remote_model_id}
    return body


def resolve_server_name(model_id: str | None) -> str:
    """Map a model ID to a human-readable server name from config."""
    cfg = load_config()
    if not model_id:
        m = cfg.models[0] if cfg.models else None
        return m.name or m.effective_id if m else "default"
    for m in cfg.models:
        if m.effective_id == model_id:
            return m.name or m.effective_id
    return model_id
