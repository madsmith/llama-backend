from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_manager.manager.llama_manager import LlamaManager

# ---------------------------------------------------------------------------
# Global LlamaManager reference (used by proxy internals)
# ---------------------------------------------------------------------------

_llama_manager: LlamaManager | None = None


def set_llama_manager(manager: LlamaManager) -> None:
    global _llama_manager
    _llama_manager = manager


def get_llama_manager() -> LlamaManager:
    assert _llama_manager is not None
    return _llama_manager
