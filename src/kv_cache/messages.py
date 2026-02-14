from __future__ import annotations

import hashlib

# Valid cacheable sequence: (system|developer)? assistant? user
_CACHEABLE_ORDER = {"system": 0, "developer": 0, "assistant": 1, "user": 2}


def is_cacheable(messages: list[dict]) -> bool:
    """A request is cacheable if messages are: [system|developer]?, assistant?, user."""
    if not messages:
        return False
    last_order = -1
    for msg in messages:
        role = msg.get("role", "")
        order = _CACHEABLE_ORDER.get(role)
        if order is None:
            return False
        if order <= last_order:
            return False  # duplicate or out-of-order
        last_order = order
    # Must end with user
    return messages[-1].get("role") == "user"


def conversation_hash(messages: list[dict]) -> str:
    """SHA-256 hash of a message sequence (used as cache/file ID)."""
    parts = []
    for msg in messages:
        parts.append(f"{msg.get('role', '')}:{msg.get('content', '')}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()
