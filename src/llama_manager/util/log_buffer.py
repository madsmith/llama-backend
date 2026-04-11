from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from llama_manager.protocol.backend import LlamaManagerProtocol

@dataclass(slots=True)
class LogLine:
    id: str
    line_number: int
    text: str
    request_id: str | None = None


class LogBuffer:
    """Thread-safe rolling log buffer with monotonic IDs."""

    def __init__(self, manager: LlamaManagerProtocol, maxlen: int = 10_000) -> None:
        self._manager: LlamaManagerProtocol = manager
        self._buf: deque[LogLine] = deque(maxlen=maxlen)
        self._next_line_no = 1
        self._lock = threading.Lock()

    def append(self, text: str, request_id: str | None = None) -> LogLine:
        with self._lock:
            id = f"{self._manager.get_manager_id()}-{self._next_line_no}"
            line = LogLine(id=id, line_number=self._next_line_no, text=text, request_id=request_id)
            self._next_line_no += 1
            self._buf.append(line)
            return line

    def snapshot(self) -> list[LogLine]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
