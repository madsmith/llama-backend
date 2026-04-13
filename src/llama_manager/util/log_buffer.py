from __future__ import annotations

import threading
import time as _time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import overload


@dataclass(slots=True)
class HttpRequest:
    method: str
    path: str
    http_ver: str = "1.1"
    size: int | None = None


@dataclass(slots=True)
class HttpResponse:
    status: int
    http_ver: str = "1.1"
    streaming: bool = False
    complete: bool = False
    elapsed: float | None = None
    size: int | None = None


@dataclass(slots=True)
class ProxyRequest(HttpRequest):
    server_name: str | None = None
    request_id: str | None = None


@dataclass(slots=True)
class ProxyResponse(HttpResponse):
    server_name: str | None = None
    request_id: str | None = None


LogRecordData = str | ProxyRequest | ProxyResponse


# TODO: consider converting back to slots=True for LogRecord and handling str conversion differently.
@dataclass
class LogRecord:
    data: LogRecordData
    request_id: str | None = None
    time: float = field(default_factory=_time.time)
    id: str = field(default="", init=False)
    line_number: int = field(default=0, init=False)

    def __str__(self) -> str:
        data = self.data
        if isinstance(data, str):
            return data
        ts = _time.strftime("%H:%M:%S", _time.localtime(self.time))
        route = f"[{data.server_name}]" if data.server_name else ""
        if isinstance(data, ProxyRequest):
            msg = f"{data.method} {data.path} HTTP/{data.http_ver}"
            if data.size is not None:
                msg += f" [{_fmt_size(data.size)}]"
            body = f"\u2192 {route} {msg}" if route else f"\u2192 {msg}"
        else:  # ProxyResponse
            if data.complete:
                msg = f"stream complete ({data.elapsed:.2f}s) [{_fmt_size(data.size or 0)}]"
            else:
                try:
                    phrase = HTTPStatus(data.status).phrase
                except ValueError:
                    phrase = ""
                msg = f"HTTP/{data.http_ver} {data.status}"
                if phrase:
                    msg += f" {phrase}"
                if data.streaming:
                    msg += " streaming"
                if data.elapsed is not None:
                    msg += f" ({data.elapsed:.2f}s)"
                if data.size is not None:
                    msg += f" [{_fmt_size(data.size)}]"
            body = f"\u2190 {route} {msg}" if route else f"\u2190 {msg}"
        return f"[{ts}] {body}"


class LogBuffer:
    """Thread-safe rolling log buffer with monotonic IDs."""

    def __init__(self, buffer_id: str, maxlen: int = 10_000) -> None:
        self._buffer_id = buffer_id
        self._buf: deque[LogRecord] = deque(maxlen=maxlen)
        self._next_line_no = 1
        self._lock = threading.Lock()

    @overload
    def append(self, data: str, request_id: str | None = None) -> LogRecord: ...
    @overload
    def append(self, data: ProxyRequest | ProxyResponse) -> LogRecord: ...

    def append(self, data: LogRecordData, request_id: str | None = None) -> LogRecord:
        if isinstance(data, (ProxyRequest, ProxyResponse)):
            request_id = data.request_id
        record = LogRecord(data=data, request_id=request_id)
        with self._lock:
            record.id = f"{self._buffer_id}-{self._next_line_no}"
            record.line_number = self._next_line_no
            self._next_line_no += 1
            self._buf.append(record)
        return record

    def snapshot(self) -> list[LogRecord]:
        with self._lock:
            return list(self._buf)

    def tail(self, n: int) -> list[LogRecord]:
        """Return the last n records."""
        with self._lock:
            items = list(self._buf)
        return items[-n:] if n < len(items) else items

    def before(self, record_id: str, n: int) -> tuple[list[LogRecord], bool]:
        """Return up to n records that appear before the record with the given id.

        Returns (records, has_more) where has_more=True if there are older
        records in the buffer beyond the returned n.  Returns ([], False) if
        record_id is not found in the buffer.
        """
        with self._lock:
            items = list(self._buf)
        idx = next((i for i, r in enumerate(items) if r.id == record_id), None)
        if idx is None:
            return [], False
        older = items[:idx]
        has_more = len(older) > n
        return (older[-n:] if n < len(older) else older), has_more

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


def _fmt_size(n: int) -> str:
    return f"{n}B" if n < 1024 else f"{n / 1024:.1f}KB"
