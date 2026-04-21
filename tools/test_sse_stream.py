#!/usr/bin/env python3
"""
Diagnostic tool: stream a chat completion and print every SSE event with
its raw hex bytes so we can spot encoding or JSON-escape issues.

Usage:
    # Through the proxy (default)
    python3 tools/test_sse_stream.py

    # Bypass proxy — hit llama-server directly
    python3 tools/test_sse_stream.py --direct --direct-url http://127.0.0.1:3210

    # Custom model / prompt
    python3 tools/test_sse_stream.py --model my-model --prompt "say hey with emoji"
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Iterator

import httpx

DEFAULT_PROXY_URL  = "http://127.0.0.1:1234"
DEFAULT_DIRECT_URL = "http://127.0.0.1:3210"
DEFAULT_MODEL      = "minimax-2.5-reap-139b-a10b"
DEFAULT_PROMPT = (
    "Reply with exactly this: Hey 👋 What can I do for you? "
    "No reasoning, no explanation, just that one line."
)


def iter_lines(resp: httpx.Response) -> Iterator[bytes]:
    buf = b""
    for raw in resp.iter_bytes():
        buf += raw
        while b"\n" in buf:
            line_bytes, buf = buf.split(b"\n", 1)
            yield line_bytes
    if buf:
        yield buf


def run(base_url: str, model: str, prompt: str, verbose: bool) -> None:
    url = f"{base_url}/v1/chat/completions"
    body = {
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
    }

    print(f"POST {url}")
    print(f"model : {model}")
    print(f"prompt: {prompt!r}")
    print("─" * 72)

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    stats = {"events": 0, "ok": 0, "utf8_err": 0, "json_err": 0, "done": False}
    last_finish_reason: str | None = None

    with httpx.Client(timeout=120) as client:
        with client.stream("POST", url, json=body) as resp:
            print(f"HTTP {resp.status_code}\n")
            for line_bytes in iter_lines(resp):
                if not line_bytes:
                    continue

                stats["events"] += 1
                n = stats["events"]

                # Decode
                try:
                    line = line_bytes.decode("utf-8")
                    decode_ok = True
                except UnicodeDecodeError:
                    line = line_bytes.decode("utf-8", errors="replace")
                    decode_ok = False
                    stats["utf8_err"] += 1

                if not line.startswith("data: "):
                    if verbose:
                        print(f"[{n:04d}] non-data: {line!r}")
                    continue

                payload = line[6:]
                if payload.strip() == "[DONE]":
                    stats["done"] = True
                    print(f"[{n:04d}] [DONE]")
                    continue

                parse_ok = True
                exc_msg = ""
                content = ""
                reasoning = ""
                finish_reason = None
                try:
                    d = json.loads(payload)
                    choice = (d.get("choices") or [{}])[0]
                    delta = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason")
                    if finish_reason:
                        last_finish_reason = finish_reason
                    c = delta.get("content")
                    r = delta.get("reasoning_content")
                    if c:
                        content = c
                        content_parts.append(c)
                    if r:
                        reasoning = r
                        reasoning_parts.append(r)
                    stats["ok"] += 1
                except (json.JSONDecodeError, IndexError) as exc:
                    parse_ok = False
                    exc_msg = str(exc)
                    stats["json_err"] += 1

                # Only print events that have something notable
                has_error = not decode_ok or not parse_ok
                has_content = bool(content)
                if has_error or has_content or finish_reason or verbose:
                    flags = []
                    if not decode_ok:
                        flags.append("UTF8-ERR")
                    if not parse_ok:
                        flags.append(f"JSON-ERR: {exc_msg}")
                    if finish_reason:
                        flags.append(f"finish={finish_reason}")
                    flag_str = f"  [{', '.join(flags)}]" if flags else ""
                    print(f"[{n:04d}] {'FAIL' if has_error else 'OK'}{flag_str}")
                    if has_error or has_content:
                        print(f"       decoded : {line!r}")
                        print(f"       hex     : {line_bytes.hex(' ')}")
                    if content:
                        print(f"       content : {content!r}")
                    if reasoning and verbose:
                        print(f"       reason  : {reasoning!r}")

    print("\n" + "─" * 72)
    print("Summary:")
    print(f"  total SSE events : {stats['events']}")
    print(f"  parsed OK        : {stats['ok']}")
    print(f"  UTF-8 errors     : {stats['utf8_err']}")
    print(f"  JSON errors      : {stats['json_err']}")
    print(f"  got [DONE]       : {stats['done']}")
    print(f"  finish_reason    : {last_finish_reason!r}")
    print(f"  reasoning chunks : {len(reasoning_parts)}")
    print(f"  content chunks   : {len(content_parts)}")
    print()
    if content_parts:
        print(f"Content ({len(content_parts)} chunks):")
        print("".join(content_parts))
    else:
        print("No content tokens received.")
    if reasoning_parts:
        joined = "".join(reasoning_parts)
        preview = joined[:300] + ("…" if len(joined) > 300 else "")
        print(f"\nReasoning preview ({len(joined)} chars):")
        print(preview)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SSE stream diagnostic")
    parser.add_argument("--url",        default=DEFAULT_PROXY_URL,
                        help="Base URL (proxy or direct)")
    parser.add_argument("--direct",     action="store_true",
                        help=f"Hit llama-server directly (uses --direct-url)")
    parser.add_argument("--direct-url", default=DEFAULT_DIRECT_URL,
                        help=f"Direct llama-server base URL (default {DEFAULT_DIRECT_URL})")
    parser.add_argument("--model",      default=DEFAULT_MODEL)
    parser.add_argument("--prompt",     default=DEFAULT_PROMPT)
    parser.add_argument("--verbose",    action="store_true",
                        help="Print every event including reasoning tokens")
    args = parser.parse_args()

    url = args.direct_url if args.direct else args.url
    if args.direct:
        print(f"[direct mode — bypassing proxy]\n")

    try:
        run(url, args.model, args.prompt, args.verbose)
    except KeyboardInterrupt:
        sys.exit(0)
