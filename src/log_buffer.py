from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class LogLine:
    id: int
    text: str


class LogBuffer:
    """Thread-safe rolling log buffer with monotonic IDs."""

    def __init__(self, maxlen: int = 10_000) -> None:
        self._buf: deque[LogLine] = deque(maxlen=maxlen)
        self._next_id = 1
        self._lock = threading.Lock()

    def append(self, text: str) -> LogLine:
        with self._lock:
            line = LogLine(id=self._next_id, text=text)
            self._next_id += 1
            self._buf.append(line)
            return line

    def snapshot(self) -> list[LogLine]:
        with self._lock:
            return list(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()
