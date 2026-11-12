#!/usr/bin/env python3
"""Interactive (or one-shot) chat client using the OpenAI chat completions API."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from typing import IO

import httpx


DEFAULT_SERVER = "http://127.0.0.1:1234"


def log_jsonl(log: IO[str], record: dict) -> None:
    log.write(json.dumps(record, ensure_ascii=False) + "\n")


class ThinkingBlock:
    """Prints reasoning as a framed block, every line prefixed with a bar:

       ,-- Thinking
       | ...
       `-- End thinking (1.2s)

    Text can arrive in arbitrary pieces (streaming). Leading and trailing
    blank lines are dropped; newlines are held until more text arrives so the
    bar is never printed for a line that never comes."""

    BAR = "   | "

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.has_text = False
        self.pending_newlines = 0
        self.col = 0
        self.width = min(shutil.get_terminal_size().columns, 120) - 1
        print("   ,-- Thinking", flush=True)

    def write(self, text: str) -> None:
        for i, piece in enumerate(text.split("\n")):
            if i > 0:
                self.pending_newlines += 1
            if piece:
                self._emit(piece)

    def _emit(self, piece: str) -> None:
        if not self.has_text:
            piece = piece.lstrip()
            if not piece:
                return
            self.pending_newlines = 0
            out = self.BAR
            self.col = len(self.BAR)
        elif self.pending_newlines:
            out = "\n" + (self.BAR.rstrip() + "\n") * (self.pending_newlines - 1) + self.BAR
            self.col = len(self.BAR)
        else:
            out = ""
        self.pending_newlines = 0
        self.has_text = True
        # Word-wrap so wrapped lines keep the bar. Words arrive whole or in
        # pieces; a piece is only broken before a space.
        for word in re.findall(r"\s*\S+|\s+", piece):
            if self.col + len(word) > self.width and self.col > len(self.BAR) and word[:1].isspace():
                out += "\n" + self.BAR
                self.col = len(self.BAR)
                word = word.lstrip()
            out += word
            self.col += len(word)
        print(out, end="", flush=True)

    def close(self, timed: bool = True) -> None:
        if self.has_text:
            print()
        took = f" ({time.monotonic() - self.t0:.1f}s)" if timed else ""
        print(f"   `-- End thinking{took}", flush=True)


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
    client: httpx.Client, base: str, body: dict, log: IO[str] | None, show_thinking: bool = False
) -> tuple[str, str, dict]:
    """Streams tokens to stdout. Returns (full_text, finish_reason, reconstructed_response)."""
    parts: list[str] = []
    reasoning_parts: list[str] = []
    finish = "?"
    resp_id = ""
    resp_model = ""
    usage: dict = {}
    timings: dict = {}
    thinking: ThinkingBlock | None = None
    content_started = False
    t0 = time.monotonic()
    t_first: float | None = None  # first reasoning or content token
    token_chunks = 0

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
            if chunk.get("timings"):
                timings = chunk["timings"]  # llama-server extension

            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            fr = choice.get("finish_reason")
            if fr:
                finish = fr

            # llama-server sends reasoning_content; vLLM and others send reasoning.
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
            token = delta.get("content") or ""
            if reasoning or token:
                token_chunks += 1
                if t_first is None:
                    t_first = time.monotonic()

            if reasoning:
                if show_thinking and thinking is None:
                    thinking = ThinkingBlock()
                if thinking is not None:
                    thinking.write(reasoning)
                reasoning_parts.append(reasoning)

            if not content_started:
                token = token.lstrip()  # models often open with a newline after thinking
            if token:
                if not content_started:
                    if thinking is not None:
                        thinking.close()
                        thinking = None
                    print("Assistant: ", end="", flush=True)
                content_started = True
                print(token, end="", flush=True)
                parts.append(token)

    if thinking is not None:
        thinking.close()
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
    if timings:
        reconstructed["timings"] = timings
    t_end = time.monotonic()
    reconstructed["_client_timing"] = {
        "ttft": (t_first - t0) if t_first is not None else None,
        "generation": (t_end - t_first) if t_first is not None else None,
        "token_chunks": token_chunks,
    }
    if reasoning_parts:
        reconstructed["choices"][0]["message"]["reasoning_content"] = "".join(reasoning_parts)
    return text, finish, reconstructed


def speed_stats(resp: dict, elapsed: float) -> str:
    """Prompt and generation tok/s: the server's own timings if it reports them
    (llama-server does), else estimated from usage and client-side timing."""
    t = resp.get("timings") or {}
    if t.get("prompt_per_second") is not None and t.get("predicted_per_second") is not None:
        cached = f" (+{t['cache_n']} cached)" if t.get("cache_n") else ""
        return (
            f"[prompt {t.get('prompt_n', '?')}tok{cached} @ {t['prompt_per_second']:.1f} tok/s  "
            f"generation {t.get('predicted_n', '?')}tok @ {t['predicted_per_second']:.1f} tok/s  "
            f"(server timings)]"
        )
    usage = resp.get("usage") or {}
    client = resp.get("_client_timing") or {}
    prompt_n = usage.get("prompt_tokens")
    gen_n = usage.get("completion_tokens") or client.get("token_chunks")
    ttft, gen_time = client.get("ttft"), client.get("generation")
    if ttft and gen_time:
        # Streaming: time to first token ~ prompt processing; the rest is generation.
        prompt = f"{prompt_n / ttft:.1f} tok/s" if prompt_n else "?"
        gen = f"{(gen_n - 1) / gen_time:.1f} tok/s" if gen_n and gen_n > 1 else "?"
        return f"[prompt {prompt_n or '?'}tok @ {prompt}  generation {gen_n or '?'}tok @ {gen}  (client estimate)]"
    if gen_n and elapsed:
        return f"[generation {gen_n}tok @ {gen_n / elapsed:.1f} tok/s incl. prompt  (client estimate; server sent no timings)]"
    return "[tok/s unavailable: server sent no timings or usage]"


def chat_turn(
    client: httpx.Client,
    base: str,
    messages: list[dict],
    model: str,
    stream: bool,
    max_tokens: int | None,
    log: IO[str] | None,
    verbose: bool = False,
) -> str:
    body: dict = {"model": model, "messages": messages, "stream": stream}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if stream and verbose:
        body["stream_options"] = {"include_usage": True}  # token counts for the estimate fallback

    t0 = time.monotonic()

    if stream:
        text, finish, full_resp = send_streaming(client, base, body, log, show_thinking=verbose)
        elapsed = time.monotonic() - t0
        stats = f"[finish={finish}  elapsed={elapsed:.2f}s]"
    else:
        text, finish, full_resp = send_blocking(client, base, body)
        elapsed = time.monotonic() - t0
        message = (full_resp.get("choices") or [{}])[0].get("message", {})
        thinking = message.get("reasoning_content") or message.get("reasoning")
        if verbose and thinking:
            block = ThinkingBlock()
            block.write(thinking)
            block.close(timed=False)  # only the whole request's time is known
        print(f"Assistant: {(text or '').lstrip()}")
        usage = full_resp.get("usage", {})
        stats = (
            f"[finish={finish}  elapsed={elapsed:.2f}s  "
            f"prompt={usage.get('prompt_tokens','?')}tok  "
            f"completion={usage.get('completion_tokens','?')}tok]"
        )

    print(stats)
    if verbose:
        print(speed_stats(full_resp, elapsed))
    print()
    full_resp.pop("_client_timing", None)

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
    parser.add_argument("-v", "--verbose", action="store_true", help="show the model's thinking and prompt/generation tok/s")
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
                    chat_turn(client, base, messages, args.model, args.stream, args.max_tokens, log, args.verbose)
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
                        reply = chat_turn(client, base, messages, args.model, args.stream, args.max_tokens, log, args.verbose)
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
