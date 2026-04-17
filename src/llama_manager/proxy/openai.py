from __future__ import annotations

import json
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable

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

    def backend_error_sse(self, msg: str) -> str:
        return f'data: {json.dumps({"error": {"message": msg, "type": "server_error"}})}\n\n'

    async def wrap_stream(
        self,
        lines: AsyncIterator[str],
        is_cancelled: Callable[[], bool],
        is_disconnected: Callable[[], Awaitable[bool]],
        on_content: Callable[[str], None],
    ) -> AsyncGenerator[str, None]:
        async for line in lines:
            if is_cancelled():
                yield f'data: {json.dumps({"error": {"message": "Request cancelled: inference terminated by server operator", "type": "capacity_exceeded", "code": "capacity_exceeded"}})}\n\n'
                return
            if await is_disconnected():
                return
            if line.startswith("data: "):
                try:
                    d = json.loads(line[6:])
                    delta = (d.get("choices") or [{}])[0].get("delta", {})
                    c = delta.get("content") or delta.get("reasoning_content")
                    if c:
                        on_content(c)
                    else:
                        for tc in delta.get("tool_calls") or []:
                            args = (tc.get("function") or {}).get("arguments")
                            if args:
                                on_content(args)
                except (json.JSONDecodeError, IndexError):
                    pass
            yield line + "\n"


