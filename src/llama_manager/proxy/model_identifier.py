"""ModelIdentifier — a stable address for a model process across federation boundaries.

A ModelIdentifier is a (manager_id, process_identifier) pair. The manager_id
is the UUID-v5 of the owning manager instance; process_identifier is a stable
string that names the process within that manager (e.g. "model-0").

String form: "{manager_id}:{process_identifier}"
This matches the existing server_id wire format used on the event bus.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelIdentifier:
    manager_id: str
    process_identifier: str

    def __str__(self) -> str:
        return f"{self.manager_id}:{self.process_identifier}"

    @classmethod
    def from_string(cls, s: str) -> ModelIdentifier:
        parts = s.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid ModelIdentifier: {s!r}")
        return cls(manager_id=parts[0], process_identifier=parts[1])
