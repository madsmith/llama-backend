from __future__ import annotations

from fastapi.responses import JSONResponse

from ...proxy.request_log import request_log


def _truncate_body(body, max_len: int = 500):
    """Truncate response body to max_len characters for list view."""
    if body is None:
        return None
    if isinstance(body, str):
        return body[:max_len] + ("..." if len(body) > max_len else "")
    if isinstance(body, dict):
        s = str(body)
        if len(s) > max_len:
            return s[:max_len] + "..."
        return body
    return body


async def list_requests():
    entries = request_log.list_entries()
    result = []
    for entry in entries:
        d = entry.to_dict()
        d["response_body"] = _truncate_body(d.get("response_body"))
        d["request_body"] = _truncate_body(d.get("request_body"))
        result.append(d)
    return result


async def get_request(request_id: str):
    entry = request_log.get(request_id)
    if entry is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return entry.to_dict()
