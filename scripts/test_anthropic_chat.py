#!/usr/bin/env python3
"""Interactive (or one-shot) chat client using the Anthropic Messages API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import IO

import httpx


DEFAULT_SERVER = "http://127.0.0.1:1234"


def log_jsonl(log: IO[str], record: dict) -> None:
    log.write(json.dumps(record, ensure_ascii=False) + "\n")


def send_blocking(client: httpx.Client, base: str, body: dict) -> tuple[str, str, dict]:
    """Returns (text, stop_reason, full_response)."""
    resp = client.post(f"{base}/v1/messages", json=body, timeout=None)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    text = "".join(
        b.get("text", "") for b in data.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    )
    stop_reason = data.get("stop_reason", "?")
    return text, stop_reason, data


def send_streaming(
    client: httpx.Client, base: str, body: dict, log: IO[str] | None
) -> tuple[str, str, dict]:
    """Streams tokens to stdout. Returns (full_text, stop_reason, reconstructed_response)."""
    parts: list[str] = []
    thinking_parts: list[str] = []
    stop_reason = "?"
    event_type = ""
    msg_id = ""
    msg_model = ""
    input_tokens = 0
    output_tokens = 0
    # Maps content block index → type ("text" | "thinking")
    block_types: dict[int, str] = {}
    in_thinking = False
    content_started = False

    with client.stream("POST", f"{base}/v1/messages", json=body, timeout=None) as resp:
        if resp.status_code != 200:
            resp.read()
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")

        for line in resp.iter_lines():
            if line.startswith("event: "):
                event_type = line[7:].strip()
                continue
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            if log:
                log_jsonl(log, {"ts": datetime.now().isoformat(timespec="milliseconds"),
                                "type": "sse_event", "event": event_type, "data": data})

            if event_type == "message_start":
                msg = data.get("message", {})
                msg_id = msg.get("id", "")
                msg_model = msg.get("model", "")
                input_tokens = msg.get("usage", {}).get("input_tokens", 0)

            elif event_type == "content_block_start":
                idx = data.get("index", 0)
                block_type = data.get("content_block", {}).get("type", "text")
                block_types[idx] = block_type
                if block_type == "thinking":
                    print("Thinking: ", end="", flush=True)
                    in_thinking = True

            elif event_type == "content_block_stop":
                idx = data.get("index", 0)
                if block_types.get(idx) == "thinking":
                    print()
                    in_thinking = False

            elif event_type == "content_block_delta":
                idx = data.get("index", 0)
                delta = data.get("delta", {})
                delta_type = delta.get("type", "")

                if delta_type == "thinking_delta":
                    token = delta.get("thinking", "")
                    if token:
                        print(token, end="", flush=True)
                        thinking_parts.append(token)

                elif delta_type == "text_delta":
                    token = delta.get("text", "")
                    if token:
                        if not content_started:
                            if in_thinking:
                                print("\nAssistant: ", end="", flush=True)
                                in_thinking = False
                            else:
                                print("Assistant: ", end="", flush=True)
                            content_started = True
                        print(token, end="", flush=True)
                        parts.append(token)

            elif event_type == "message_delta":
                stop_reason = data.get("delta", {}).get("stop_reason", stop_reason)
                output_tokens = data.get("usage", {}).get("output_tokens", output_tokens)

            elif event_type == "error":
                err = data.get("error", {})
                raise RuntimeError(f"{err.get('type', 'error')}: {err.get('message', str(data))}")

    if not content_started:
        print("Assistant: ", end="", flush=True)
    print()

    text = "".join(parts)
    content_blocks: list[dict] = []
    if thinking_parts:
        content_blocks.append({"type": "thinking", "thinking": "".join(thinking_parts)})
    content_blocks.append({"type": "text", "text": text})

    reconstructed = {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": msg_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return text, stop_reason, reconstructed


def chat_turn(
    client: httpx.Client,
    base: str,
    messages: list[dict],
    model: str,
    system: str | None,
    stream: bool,
    max_tokens: int | None,
    log: IO[str] | None,
) -> str:
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "max_tokens": max_tokens or 8192,
    }
    if system:
        body["system"] = system

    t0 = time.monotonic()

    if stream:
        text, stop_reason, full_resp = send_streaming(client, base, body, log)
        elapsed = time.monotonic() - t0
        stats = f"[stop={stop_reason}  elapsed={elapsed:.2f}s]"
    else:
        print("Assistant: ", end="", flush=True)
        text, stop_reason, full_resp = send_blocking(client, base, body)
        elapsed = time.monotonic() - t0
        print(text)
        usage = full_resp.get("usage", {})
        stats = (
            f"[stop={stop_reason}  elapsed={elapsed:.2f}s  "
            f"input={usage.get('input_tokens','?')}tok  "
            f"output={usage.get('output_tokens','?')}tok]"
        )

    print(f"{stats}\n")

    if log:
        log_jsonl(log, {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed": round(elapsed, 3),
            "type": "exchange",
            "request": body,
            "response": full_resp,
        })

    return text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Anthropic chat client — interactive by default, one-shot if a message is given",
        epilog=(
            "examples:\n"
            "  %(prog)s                                  # interactive chat\n"
            "  %(prog)s 'What is 2+2?'                   # one-shot\n"
            "  %(prog)s -m llama-3 --system 'Be brief'   # interactive with system prompt\n"
            "  %(prog)s -m llama-3 --stream 'Tell me a joke'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-m", "--model", default="gemma-4-31b", metavar="MODEL", help="model ID (default: gemma-4-31b)")
    parser.add_argument("message", nargs="?", help="user message — omit for interactive mode")
    parser.add_argument("--system", metavar="TEXT", help="system / agent prompt")
    parser.add_argument("--stream", action="store_true", help="use streaming (SSE)")
    parser.add_argument("--max-tokens", type=int, metavar="N", help="max tokens per response (default: 8192)")
    parser.add_argument("--log", metavar="FILE", help="append exchanges to this file as JSONL")
    parser.add_argument("--server", default=DEFAULT_SERVER, metavar="URL",
                        help=f"server base URL (default: {DEFAULT_SERVER})")
    args = parser.parse_args()

    base = args.server.rstrip("/")
    one_shot = args.message is not None

    print(f"Server : {base}")
    print(f"Model  : {args.model}")
    print(f"Stream : {args.stream}")
    if args.system:
        print(f"System : {args.system}")
    if not one_shot:
        print("Type /quit or Ctrl-D to exit.")
    print()

    messages: list[dict] = []

    log: IO[str] | None = None
    if args.log:
        log = open(args.log, "a", buffering=1)

    try:
        with httpx.Client() as client:
            if one_shot:
                messages.append({"role": "user", "content": args.message})
                try:
                    chat_turn(client, base, messages, args.model, args.system, args.stream, args.max_tokens, log)
                except RuntimeError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    sys.exit(1)
            else:
                while True:
                    try:
                        user_input = input("You: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        break
                    if not user_input:
                        continue
                    if user_input.lower() in ("/quit", "/exit", "/q"):
                        break
                    messages.append({"role": "user", "content": user_input})
                    try:
                        reply = chat_turn(client, base, messages, args.model, args.system, args.stream, args.max_tokens, log)
                        messages.append({"role": "assistant", "content": reply})
                    except RuntimeError as exc:
                        print(f"ERROR: {exc}\n", file=sys.stderr)
    except httpx.ConnectError:
        print(f"ERROR: could not connect to {base}", file=sys.stderr)
        sys.exit(1)
    finally:
        if log:
            log.close()


if __name__ == "__main__":
    main()
