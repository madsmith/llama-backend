from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

from llama_manager.config import ModelConfig


class OpenAIAdapter:
    """ProtocolAdapter for the OpenAI API — passes through OAI format as-is."""

    def prepare_body(self, body: dict, model_config: ModelConfig | None) -> dict:
        return self._normalize_messages(body, model_config)

    @staticmethod
    def _remap_developer_role(messages: list[dict]) -> list[dict]:
        return [
            {**msg, "role": "system"} if msg.get("role") == "developer" else msg
            for msg in messages
        ]

    def _normalize_messages(self, body: dict, model_config: ModelConfig | None) -> dict:
        """Rewrite messages so llama-server's Jinja template can handle them.

        1. Remap developer → system (if model lacks developer role support).
        2. Flatten content arrays to plain strings.
        """
        messages = body.get("messages")
        if not messages:
            return body

        supports_dev = model_config is not None and model_config.advanced.supports_developer_role
        if not supports_dev:
            messages = self._remap_developer_role(messages)

        out = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                msg = {**msg, "content": content}
            out.append(msg)
        return {**body, "messages": out}

    def translate_response(self, resp_json: dict) -> dict:
        return resp_json

    def error_body(self, status: int, msg: str) -> dict:
        return {"error": {"message": msg, "type": "not_found" if status == 404 else "server_error"}}

    def backend_error_sse(self, msg: str) -> bytes:
        return f'data: {json.dumps({"error": {"message": f"llama-manager backend error: {msg}", "type": "server_error"}})}\n\n'.encode()

    async def wrap_stream(
        self,
        chunks: AsyncIterator[bytes],
        is_cancelled: Callable[[], bool],
        is_disconnected: Callable[[], Awaitable[bool]],
        on_content: Callable[[str], None],
    ) -> AsyncGenerator[bytes, None]:
        # Accumulate raw bytes and split on b"\n" at the byte level.
        # 0x0A cannot appear inside a UTF-8 multi-byte sequence, so this is
        # safe and avoids any bytes→str→bytes round-trip that could corrupt
        # non-ASCII content (e.g. emoji) via encoding replacement errors.
        buf = b""
        async for raw in chunks:
            buf += raw
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                if is_cancelled():
                    yield (
                        b'data: ' + json.dumps({
                            "error": {
                                "message": "Request cancelled: inference terminated by server operator",
                                "type": "capacity_exceeded",
                                "code": "capacity_exceeded",
                            }
                        }).encode() + b'\n\n'
                    )
                    return
                if await is_disconnected():
                    return
                line = line_bytes.decode("utf-8", errors="replace")
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        pass  # normal terminal SSE event
                    else:
                        try:
                            d = json.loads(payload)
                            delta = (d.get("choices") or [{}])[0].get("delta", {})
                            c = delta.get("content") or delta.get("reasoning_content")
                            if c:
                                on_content(c)
                            else:
                                for tc in delta.get("tool_calls") or []:
                                    args = (tc.get("function") or {}).get("arguments")
                                    if args:
                                        on_content(args)
                        except (json.JSONDecodeError, IndexError) as exc:
                            logger.warning(
                                "SSE JSON parse error: %s | decoded: %r | hex: %s",
                                exc, line, line_bytes.hex(" "),
                            )
                yield line_bytes + b"\n"
        if buf:
            yield buf


