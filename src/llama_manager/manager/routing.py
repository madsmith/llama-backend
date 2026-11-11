from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_manager.library import LibraryEntry
    from llama_manager.protocol.backend import Backend


@dataclass(eq=False)
class RouteCandidate:
    """A server that could take a request: a backend, or a library model not created yet."""
    priority: int
    backend: Backend | None = None
    entry: LibraryEntry | None = None  # local autoload model with no server object yet
    failed: bool = False  # couldn't start for this request; skip it
