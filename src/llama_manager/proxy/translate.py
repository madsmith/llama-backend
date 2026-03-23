from __future__ import annotations

import json
import uuid

from ..config import load_config

# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Anthropic → OpenAI translation
# ---------------------------------------------------------------------------


def anthropic_to_openai(body: dict) -> dict:
    """Convert an Anthropic Messages API request to OpenAI chat/completions."""
    messages: list[dict] = []
    if system := body.get("system"):
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            text = "\n\n".join(
                b.get("text", "") for b in system if b.get("type") == "text"
            )
            if text:
                messages.append({"role": "system", "content": text})
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = "\n".join(parts) if parts else ""
        messages.append({"role": msg["role"], "content": content})

    oai: dict = {
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
    }
    if body.get("model"):
        oai["model"] = body["model"]
    if body.get("temperature") is not None:
        oai["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        oai["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]
    if body.get("stream"):
        oai["stream"] = True
        oai["stream_options"] = {"include_usage": True}
    return oai


# ---------------------------------------------------------------------------
# OpenAI → Anthropic translation
# ---------------------------------------------------------------------------

FINISH_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "end_turn",
}


def openai_to_anthropic(oai_resp: dict, model: str) -> dict:
    """Convert an OpenAI chat/completions response to Anthropic Messages format."""
    choice = oai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    text = message.get("content", "") or ""
    finish = choice.get("finish_reason", "stop") or "stop"
    usage_in = oai_resp.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": FINISH_MAP.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage_in.get("prompt_tokens", 0),
            "output_tokens": usage_in.get("completion_tokens", 0),
        },
    }


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
