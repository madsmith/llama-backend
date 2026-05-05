#!/usr/bin/env python3
"""Interactive (or one-shot) chat client using the OpenAI chat completions API."""

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
    """Returns (text, finish_reason, full_response)."""
    resp = client.post(f"{base}/v1/chat/completions", json=body, timeout=None)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = choice.get("message", {}).get("content", "")
    finish = choice.get("finish_reason", "?")
    return text, finish, data


def send_streaming(
    client: httpx.Client, base: str, body: dict, log: IO[str] | None
) -> tuple[str, str, dict]:
    """Streams tokens to stdout. Returns (full_text, finish_reason, reconstructed_response)."""
    parts: list[str] = []
    reasoning_parts: list[str] = []
    finish = "?"
    resp_id = ""
    resp_model = ""
    usage: dict = {}
    in_reasoning = False
    content_started = False

    with client.stream("POST", f"{base}/v1/chat/completions", json=body, timeout=None) as resp:
        if resp.status_code != 200:
            resp.read()
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if log:
                log_jsonl(log, {"ts": datetime.now().isoformat(timespec="milliseconds"),
                                "type": "sse_chunk", "data": chunk})

            if not resp_id:
                resp_id = chunk.get("id", "")
                resp_model = chunk.get("model", "")
            if chunk.get("usage"):
                usage = chunk["usage"]

            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            fr = choice.get("finish_reason")
            if fr:
                finish = fr

            reasoning = delta.get("reasoning_content", "")
            token = delta.get("content", "")

            if reasoning:
                if not in_reasoning:
                    print("Thinking: ", end="", flush=True)
                    in_reasoning = True
                print(reasoning, end="", flush=True)
                reasoning_parts.append(reasoning)

            if token:
                if in_reasoning:
                    print("\nAssistant: ", end="", flush=True)
                    in_reasoning = False
                elif not content_started:
                    print("Assistant: ", end="", flush=True)
                content_started = True
                print(token, end="", flush=True)
                parts.append(token)

    if not content_started:
        print("Assistant: ", end="", flush=True)
    print()

    text = "".join(parts)
    reconstructed: dict = {
        "id": resp_id,
        "object": "chat.completion",
        "model": resp_model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": usage,
    }
    if reasoning_parts:
        reconstructed["choices"][0]["message"]["reasoning_content"] = "".join(reasoning_parts)
    return text, finish, reconstructed


def chat_turn(
    client: httpx.Client,
    base: str,
    messages: list[dict],
    model: str,
    stream: bool,
    max_tokens: int | None,
    log: IO[str] | None,
) -> str:
    body: dict = {"model": model, "messages": messages, "stream": stream}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    t0 = time.monotonic()

    if stream:
        text, finish, full_resp = send_streaming(client, base, body, log)
        elapsed = time.monotonic() - t0
        stats = f"[finish={finish}  elapsed={elapsed:.2f}s]"
    else:
        print("Assistant: ", end="", flush=True)
        text, finish, full_resp = send_blocking(client, base, body)
        elapsed = time.monotonic() - t0
        print(text)
        usage = full_resp.get("usage", {})
        stats = (
            f"[finish={finish}  elapsed={elapsed:.2f}s  "
            f"prompt={usage.get('prompt_tokens','?')}tok  "
            f"completion={usage.get('completion_tokens','?')}tok]"
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
        description="OpenAI chat client — interactive by default, one-shot if a message is given",
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
    parser.add_argument("--max-tokens", type=int, metavar="N", help="max tokens per response")
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
    if args.system:
        messages.append({"role": "system", "content": args.system})

    log: IO[str] | None = None
    if args.log:
        log = open(args.log, "a", buffering=1)

    try:
        with httpx.Client() as client:
            if one_shot:
                messages.append({"role": "user", "content": args.message})
                try:
                    chat_turn(client, base, messages, args.model, args.stream, args.max_tokens, log)
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
                        reply = chat_turn(client, base, messages, args.model, args.stream, args.max_tokens, log)
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
