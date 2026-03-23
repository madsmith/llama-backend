"""ModelRegistry — singleton lookup for all process managers and remote proxies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .identifier import ModelIdentifier

if TYPE_CHECKING:
    from llama_manager.process_manager import ProcessManager
    from llama_manager.remote_manager_client import RemoteModelProxy

ProcessEntry = "ProcessManager | RemoteModelProxy | None"


class ModelRegistry:
    """Central lookup for all registered process managers and remote proxies.

    Replaces scattered pms[index] array accesses and the resolve_model_index()
    pattern.  Each entry must expose .model_identifier (ModelIdentifier),
    .model_index (int for port/config lookup), and duck-type ProcessManager.

    Obtain the singleton via ModelRegistry.get_registry().
    """

    _instance: ModelRegistry | None = None

    @classmethod
    def get_registry(cls) -> ModelRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._entries: list[ProcessManager | RemoteModelProxy | None] = []

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_entries(self, entries: list[ProcessManager | RemoteModelProxy | None]) -> None:
        """Bind to the canonical process managers list (store reference, not copy).

        remote_manager_client mutates this list in-place when remotes connect/disconnect,
        so we hold the same object to stay in sync automatically.
        """
        self._entries = entries

    def add(self, entry: ProcessManager | RemoteModelProxy) -> None:
        self._entries.append(entry)

    def remove(self, entry: ProcessManager | RemoteModelProxy) -> None:
        try:
            self._entries.remove(entry)
        except ValueError:
            pass

    def replace_at(self, index: int, entry: ProcessManager | RemoteModelProxy | None) -> None:
        """Replace the entry at a given list position (used during config sync)."""
        if index < len(self._entries):
            self._entries[index] = entry

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def all(self) -> list[ProcessManager | RemoteModelProxy | None]:
        return list(self._entries)

    def get_by_local_index(self, index: int) -> ProcessManager | RemoteModelProxy | None:
        """Return the entry at position *index* in the process managers list."""
        if index < 0 or index >= len(self._entries):
            return None
        return self._entries[index]

    def get_by_process_identifier(self, process_identifier: str) -> ProcessManager | RemoteModelProxy | None:
        """Return the entry whose process_identifier matches."""
        for e in self._entries:
            if getattr(e, "process_manager_id", None) == process_identifier:
                return e
        return None

    def get_by_model_identifier(self, identifier: ModelIdentifier) -> ProcessManager | RemoteModelProxy | None:
        """Return the entry matching a full ModelIdentifier."""
        for e in self._entries:
            mid = getattr(e, "model_identifier", None)
            if mid == identifier:
                return e
        return None

    def get_by_model_id(self, model_id: str | None) -> ProcessManager | RemoteModelProxy | None:
        """Return the entry whose exposed model_id matches (for proxy routing)."""
        from ..config import load_config
        from ..remote_manager_client import RemoteModelProxy

        if not model_id:
            return self.get_by_local_index(0)

        # Config-defined local/remote models
        cfg = load_config()
        for i, m in enumerate(cfg.models):
            if m.effective_id == model_id:
                return self.get_by_local_index(i)

        # Federated remote proxies
        for e in self._entries:
            if isinstance(e, RemoteModelProxy) and e.model_id == model_id:
                return e

        return None
