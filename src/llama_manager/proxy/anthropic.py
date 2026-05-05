from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

from llama_manager.config import ModelConfig


def _map_finish_reason(finish_reason: str | None) -> str | None:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }.get(finish_reason or "")


def _flatten_text_blocks(content: list) -> str:
    return "\n".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class AnthropicAdapter:
    """ProtocolAdapter for the Anthropic Messages API.

    Translates between the Anthropic /v1/messages wire format and the
    OpenAI-compatible format that llama-server speaks.
    """

    def prepare_body(self, body: dict, model_config: ModelConfig | None) -> dict:
        messages = list(body.get("messages", []))

        system = body.get("system")
        if system:
            text = system if isinstance(system, str) else _flatten_text_blocks(system)
            messages = [{"role": "system", "content": text}] + messages

        oai_messages: list[dict] = []
        for msg in messages:
            oai_messages.extend(self._convert_message(msg.get("role", ""), msg.get("content")))

        result: dict = {
            "model": body.get("model", ""),
            "messages": oai_messages,
            "stream": body.get("stream", False),
        }

        for key in ("temperature", "top_p", "top_k", "max_tokens"):
            if key in body:
                result[key] = body[key]

        if "stop_sequences" in body:
            result["stop"] = body["stop_sequences"]

        if "tools" in body:
            result["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                }
                for t in body["tools"]
            ]

        if "tool_choice" in body:
            tc = body["tool_choice"]
            if isinstance(tc, str):
                result["tool_choice"] = {"auto": "auto", "any": "required", "none": "none"}.get(tc, "auto")
            elif isinstance(tc, dict) and tc.get("type") == "tool":
                result["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}

        return result

    @staticmethod
    def _convert_message(role: str, content) -> list[dict]:
        if role == "system":
            text = content if isinstance(content, str) else _flatten_text_blocks(content or [])
            return [{"role": "system", "content": text}]

        if role == "user":
            if isinstance(content, str):
                return [{"role": "user", "content": content}]
            if isinstance(content, list):
                tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                out: list[dict] = []
                for tr in tool_results:
                    tr_content = tr.get("content", "")
                    if isinstance(tr_content, list):
                        tr_content = _flatten_text_blocks(tr_content)
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tr_content,
                    })
                if text_blocks:
                    out.append({"role": "user", "content": _flatten_text_blocks(text_blocks)})
                return out
            return [{"role": "user", "content": str(content)}]

        if role == "assistant":
            if isinstance(content, str):
                return [{"role": "assistant", "content": content}]
            if isinstance(content, list):
                text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                text = _flatten_text_blocks(text_blocks) or None
                if tool_uses:
                    tool_calls = [
                        {
                            "id": b.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": b.get("name", ""),
                                "arguments": json.dumps(b.get("input", {})),
                            },
                        }
                        for b in tool_uses
                    ]
                    return [{"role": "assistant", "content": text, "tool_calls": tool_calls}]
                return [{"role": "assistant", "content": text or ""}]
            return [{"role": "assistant", "content": str(content)}]

        return [{"role": role, "content": content if isinstance(content, str) else str(content)}]

    def translate_response(self, resp_json: dict) -> dict:
        choices = resp_json.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason")

        content_blocks: list[dict] = []
        text = message.get("content")
        if text:
            content_blocks.append({"type": "text", "text": text})

        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                input_data = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                input_data = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": input_data,
            })

        usage = resp_json.get("usage") or {}
        return {
            "id": resp_json.get("id", f"msg_{uuid.uuid4().hex[:12]}"),
            "type": "message",
            "role": "assistant",
            "content": content_blocks,
            "model": resp_json.get("model", ""),
            "stop_reason": _map_finish_reason(finish_reason),
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        }

    def error_body(self, status: int, msg: str) -> dict:
        error_type = {
            400: "invalid_request_error",
            401: "authentication_error",
            404: "not_found_error",
            429: "rate_limit_error",
        }.get(status, "api_error")
        return {"type": "error", "error": {"type": error_type, "message": msg}}

    def backend_error_sse(self, msg: str) -> bytes:
        return _sse("error", {
            "type": "error",
            "error": {"type": "overloaded_error", "message": f"llama-manager backend error: {msg}"},
        })

    async def wrap_stream(
        self,
        chunks: AsyncIterator[bytes],
        is_cancelled: Callable[[], bool],
        is_disconnected: Callable[[], Awaitable[bool]],
        on_content: Callable[[str], None],
    ) -> AsyncGenerator[bytes, None]:
        buf = b""
        msg_started = False
        text_block_idx: int | None = None
        # Maps OpenAI tool_calls[].index → content block index
        tool_block_idxs: dict[int, int] = {}
        tool_ids: dict[int, str] = {}
        tool_names: dict[int, str] = {}
        next_block = 0
        finish_reason: str | None = None
        output_tokens = 0

        async for raw in chunks:
            buf += raw
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                if is_cancelled():
                    yield _sse("error", {
                        "type": "error",
                        "error": {
                            "type": "overloaded_error",
                            "message": "Request cancelled: inference terminated by server operator",
                        },
                    })
                    return
                if await is_disconnected():
                    return

                line = line_bytes.decode("utf-8", errors="replace")
                if not line.startswith("data: "):
                    yield line_bytes + b"\n"
                    continue

                payload = line[6:].strip()
                if payload == "[DONE]":
                    if text_block_idx is not None:
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})
                    for ti in sorted(tool_block_idxs):
                        yield _sse("content_block_stop", {"type": "content_block_stop", "index": tool_block_idxs[ti]})
                    stop_reason = _map_finish_reason(finish_reason) or "end_turn"
                    yield _sse("message_delta", {
                        "type": "message_delta",
                        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                        "usage": {"output_tokens": output_tokens},
                    })
                    yield _sse("message_stop", {"type": "message_stop"})
                    return

                try:
                    d = json.loads(payload)
                except json.JSONDecodeError as exc:
                    logger.warning("AnthropicAdapter SSE parse error: %s | payload: %r", exc, payload)
                    continue

                if not msg_started:
                    model = d.get("model", "")
                    msg_id = d.get("id", f"msg_{uuid.uuid4().hex[:12]}")
                    yield _sse("message_start", {
                        "type": "message_start",
                        "message": {
                            "id": msg_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    })
                    yield _sse("ping", {"type": "ping"})
                    msg_started = True

                usage = d.get("usage") or {}
                if "completion_tokens" in usage:
                    output_tokens = usage["completion_tokens"]

                choices = d.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                fr = choice.get("finish_reason")
                if fr:
                    finish_reason = fr

                text = delta.get("content")
                if text:
                    on_content(text)
                    if text_block_idx is None:
                        text_block_idx = next_block
                        next_block += 1
                        yield _sse("content_block_start", {
                            "type": "content_block_start",
                            "index": text_block_idx,
                            "content_block": {"type": "text", "text": ""},
                        })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": text_block_idx,
                        "delta": {"type": "text_delta", "text": text},
                    })

                for tc in delta.get("tool_calls") or []:
                    tc_idx = tc.get("index", 0)
                    tc_id = tc.get("id")
                    fn = tc.get("function") or {}
                    tc_name = fn.get("name")
                    arguments = fn.get("arguments", "")

                    if tc_idx not in tool_block_idxs:
                        if text_block_idx is not None:
                            yield _sse("content_block_stop", {
                                "type": "content_block_stop",
                                "index": text_block_idx,
                            })
                            text_block_idx = None
                        tool_ids[tc_idx] = tc_id or ""
                        tool_names[tc_idx] = tc_name or ""
                        tool_block_idxs[tc_idx] = next_block
                        next_block += 1
                        yield _sse("content_block_start", {
                            "type": "content_block_start",
                            "index": tool_block_idxs[tc_idx],
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_ids[tc_idx],
                                "name": tool_names[tc_idx],
                                "input": {},
                            },
                        })

                    if tc_id:
                        tool_ids[tc_idx] = tc_id
                    if tc_name:
                        tool_names[tc_idx] = tc_name

                    if arguments:
                        on_content(arguments)
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": tool_block_idxs[tc_idx],
                            "delta": {"type": "input_json_delta", "partial_json": arguments},
                        })

        if buf:
            yield buf
