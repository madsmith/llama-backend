from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RequestLogEntry:
    request_id: str
    timestamp: float
    model_id: str | None = None
    request_headers: dict = field(default_factory=dict)
    request_body: dict | None = None
    response_status: int | None = None
    response_headers: dict | None = None
    response_body: dict | str | None = None
    streaming: bool = False
    elapsed: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class RequestLog:
    """Thread-safe rotating log of proxy requests."""

    def __init__(self, maxlen: int = 100):
        self._maxlen = maxlen
        self._entries: OrderedDict[str, RequestLogEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._save_logs = False

    def enable_save_logs(self) -> None:
        self._save_logs = True
        os.makedirs("logs", exist_ok=True)

    def _write_log(self, entry: RequestLogEntry) -> None:
        path = os.path.join("logs", f"{entry.request_id}.json")
        try:
            with open(path, "w") as f:
                json.dump(entry.to_dict(), f, indent=2)
        except OSError:
            logger.exception("Failed to write request log to %s", path)

    def create(
        self,
        request_id: str,
        headers: dict,
        body: dict | None = None,
        model_id: str | None = None,
    ) -> RequestLogEntry:
        entry = RequestLogEntry(
            request_id=request_id,
            timestamp=time.time(),
            model_id=model_id,
            request_headers=headers,
            request_body=body,
        )
        with self._lock:
            self._entries[request_id] = entry
            while len(self._entries) > self._maxlen:
                self._entries.popitem(last=False)
        if self._save_logs:
            self._write_log(entry)
        return entry


    def update(self, request_id: str, **kwargs) -> None:
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                return
            for key, value in kwargs.items():
                if hasattr(entry, key):
                    setattr(entry, key, value)
        if self._save_logs and entry is not None:
            self._write_log(entry)


    def get(self, request_id: str) -> RequestLogEntry | None:
        with self._lock:
            return self._entries.get(request_id)


    def list_entries(self) -> list[RequestLogEntry]:
        with self._lock:
            return list(reversed(self._entries.values()))
