from __future__ import annotations

from llama_manager.config import load_config


# ---------------------------------------------------------------------------
# Normalize OpenAI messages for llama-server compatibility
# ---------------------------------------------------------------------------


def _remap_developer_role(messages: list[dict]) -> list[dict]:
    """Map 'developer' role to 'system' for models that don't support it."""
    return [
        {**msg, "role": "system"} if msg.get("role") == "developer" else msg
        for msg in messages
    ]


def normalize_messages(body: dict, model_index: int | None) -> dict:
    """Rewrite messages so llama-server's Jinja template can handle them.

    1. Remap developer → system (if model lacks developer role support).
    2. Flatten content arrays to plain strings.
    """
    messages = body.get("messages")
    if not messages:
        return body

    # Check if the target model supports the developer role
    cfg = load_config()
    supports_dev = False
    if model_index is not None and model_index < len(cfg.models):
        supports_dev = cfg.models[model_index].advanced.supports_developer_role

    if not supports_dev:
        messages = _remap_developer_role(messages)

    out = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            # [{"type":"text","text":"..."},...]  →  "..."
            content = "\n".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
            msg = {**msg, "content": content}
        out.append(msg)
    return {**body, "messages": out}
