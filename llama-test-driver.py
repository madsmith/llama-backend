#!/usr/bin/env python3
"""Drive concurrent chat completions against a llama-server and optionally query /slots and /models."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

from pathlib import Path

import httpx

TOPICS = [
    "the history of bread making",
    "how black holes form and evolve",
    "the migration patterns of monarch butterflies",
    "the invention of the printing press",
    "deep sea bioluminescence",
    "the mathematics of fractals",
    "ancient Roman aqueduct engineering",
    "the psychology of procrastination",
    "how compilers optimize code",
    "the ecology of coral reefs",
    "the physics of guitar strings",
    "fermentation and its role in human civilization",
    "the architecture of Gothic cathedrals",
    "how immune systems fight viruses",
    "the economics of space exploration",
    "mushroom mycelium networks in forests",
    "the history of cryptography",
    "plate tectonics and continental drift",
    "the neuroscience of dreams",
    "how transistors work at the atomic level",
]


def make_prompt(topic: str, words: int) -> str:
    return f"Generate {words} words about {topic}."


async def run_completion(
    client: httpx.AsyncClient,
    base: str,
    prompt: str,
    index: int,
    model: str | None,
    output_dir: Path | None,
) -> None:
    stream = output_dir is not None
    body: dict = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
    }
    if model:
        body["model"] = model

    t0 = time.monotonic()
    tag = f"[req {index}]"
    print(f"{tag} sending: {prompt[:80]}...")

    try:
        if stream:
            await _run_streaming(client, base, body, index, tag, t0, output_dir)
        else:
            await _run_blocking(client, base, body, tag, t0)
    except httpx.ConnectError:
        print(f"{tag} ERROR: could not connect to {base}")
    except Exception as exc:
        print(f"{tag} ERROR: {exc}")


async def _run_blocking(
    client: httpx.AsyncClient,
    base: str,
    body: dict,
    tag: str,
    t0: float,
) -> None:
    resp = await client.post(f"{base}/v1/chat/completions", json=body, timeout=None)
    elapsed = time.monotonic() - t0
    if resp.status_code != 200:
        print(f"{tag} ERROR {resp.status_code}: {resp.text[:200]}")
        return
    data = resp.json()
    choice = data.get("choices", [{}])[0]
    text = choice.get("message", {}).get("content", "")
    usage = data.get("usage", {})
    words = len(text.split())
    print(
        f"{tag} done in {elapsed:.1f}s  "
        f"words={words}  "
        f"prompt_tokens={usage.get('prompt_tokens', '?')}  "
        f"completion_tokens={usage.get('completion_tokens', '?')}"
    )


async def _run_streaming(
    client: httpx.AsyncClient,
    base: str,
    body: dict,
    index: int,
    tag: str,
    t0: float,
    output_dir: Path,
) -> None:
    out_path = output_dir / f"prompt_{index}.txt"
    total_tokens = 0
    async with client.stream("POST", f"{base}/v1/chat/completions", json=body, timeout=None) as resp:
        if resp.status_code != 200:
            await resp.aread()
            print(f"{tag} ERROR {resp.status_code}: {resp.text[:200]}")
            return
        with open(out_path, "w") as f:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    f.write(content)
                    f.flush()
                    total_tokens += 1
    elapsed = time.monotonic() - t0
    size = out_path.stat().st_size
    print(f"{tag} done in {elapsed:.1f}s  chunks={total_tokens}  written={out_path} ({size} bytes)")


def print_slots(slots: list) -> None:
    print(f"\n{'=' * 70}")
    print(f"[slots] {len(slots)} slot(s) at {time.strftime('%H:%M:%S')}")
    for s in slots:
        processing = s.get("is_processing", False)
        nt = s.get("next_token", {})
        if isinstance(nt, list):
            nt = nt[0] if nt else {}
        decoded = nt.get("n_decoded", 0)
        remain = nt.get("n_remain", "?")
        status = "BUSY" if processing else "idle"
        print(f"  slot {s['id']}: {status:4s}  decoded={decoded}  remain={remain}")
    print(f"{'=' * 70}\n")


def print_models(data: dict) -> None:
    models = data.get("data", [])
    print(f"\n{'=' * 70}")
    print(f"[models] {len(models)} model(s)")
    for m in models:
        mid = m.get("id", "?")
        owner = m.get("owned_by", "?")
        print(f"  {mid}  (owned_by: {owner})")
    print(f"{'=' * 70}\n")


async def fetch_slots(client: httpx.AsyncClient, base: str, raw: bool) -> None:
    try:
        resp = await client.get(f"{base}/slots", timeout=5)
        if resp.status_code != 200:
            print(f"\n[slots] ERROR {resp.status_code}")
            return
        data = resp.json()
        if raw:
            print(json.dumps(data, indent=2))
        else:
            print_slots(data)
    except Exception as exc:
        print(f"\n[slots] error: {exc}\n")


async def fetch_models(client: httpx.AsyncClient, base: str, raw: bool) -> None:
    try:
        resp = await client.get(f"{base}/v1/models", timeout=5)
        if resp.status_code != 200:
            print(f"\n[models] ERROR {resp.status_code}")
            return
        data = resp.json()
        if raw:
            print(json.dumps(data, indent=2))
        else:
            print_models(data)
    except Exception as exc:
        print(f"\n[models] error: {exc}\n")


async def show_once(client: httpx.AsyncClient, base: str,
                    show_slots: bool, show_models: bool, raw: bool) -> None:
    if show_slots:
        await fetch_slots(client, base, raw)
    if show_models:
        await fetch_models(client, base, raw)


async def poll_loop(client: httpx.AsyncClient, base: str, interval: float,
                    show_slots: bool, show_models: bool, raw: bool) -> None:
    while True:
        await asyncio.sleep(interval)
        await show_once(client, base, show_slots, show_models, raw)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Drive concurrent prompts against a llama-server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s http://127.0.0.1:3210 -n 4\n"
            "  %(prog)s http://127.0.0.1:3210 --show-slots                        # immediate one-shot\n"
            "  %(prog)s http://127.0.0.1:3210 --show-slots --show-delay 5          # once after 5s\n"
            "  %(prog)s http://127.0.0.1:3210 --show-slots --show-interval 2       # repeat every 2s\n"
            "  %(prog)s http://127.0.0.1:3210 -n 2 --show-slots --show-interval 2  # infer + poll\n"
            '  %(prog)s http://127.0.0.1:3210 --prompt "Write a poem" --prompt "Write a story"\n'
        ),
    )
    parser.add_argument("server", help="llama-server address (e.g. http://127.0.0.1:3210)")
    parser.add_argument("-n", "--num", type=int, default=None, help="number of concurrent requests")
    parser.add_argument("-w", "--words", type=int, default=2000, help="word count for generated prompts (default: 2000)")
    parser.add_argument("--prompt", action="append", default=[], help="explicit prompt for request N (repeatable)")
    parser.add_argument("--model", action="append", default=[], help="model ID for request N (repeatable, cycles)")
    parser.add_argument("-o", "--output", default=None, help="output directory for streamed responses (prompt_<n>.txt)")
    parser.add_argument("--show-slots", action="store_true", help="query /slots")
    parser.add_argument("--show-models", action="store_true", help="query /v1/models")
    parser.add_argument("--show-delay", type=float, default=None, help="wait N seconds before displaying (once)")
    parser.add_argument("--show-interval", type=float, default=None, help="repeat display every N seconds")
    parser.add_argument("--raw", action="store_true", help="pretty-print full JSON for --show-* commands")
    args = parser.parse_args()

    base = args.server.rstrip("/")

    # Determine how many inference requests to run
    n = args.num if args.num is not None else max(len(args.prompt), len(args.model))

    # Build prompt list: explicit prompts first, then random topics
    prompts: list[str] = list(args.prompt)
    used_topics = set()
    while len(prompts) < n:
        available = [t for t in TOPICS if t not in used_topics]
        if not available:
            available = TOPICS
        topic = random.choice(available)
        used_topics.add(topic)
        prompts.append(make_prompt(topic, args.words))

    # Build per-request model list (cycles through --model values)
    models: list[str | None] = [None] * n
    if args.model:
        for i in range(n):
            models[i] = args.model[i % len(args.model)]

    show_any = args.show_slots or args.show_models

    output_dir: Path | None = None
    if args.output:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Target: {base}")
    if n > 0:
        print(f"Concurrent requests: {n}")
    if args.model:
        print(f"Models: {', '.join(args.model)}")
    if output_dir:
        print(f"Output: {output_dir}/")
    print()

    async with httpx.AsyncClient() as client:
        # Launch inference requests
        tasks = [
            asyncio.create_task(run_completion(client, base, prompts[i], i, models[i], output_dir))
            for i in range(n)
        ]

        # Handle --show-* display
        poll_task = None
        if show_any:
            if args.show_interval is not None:
                # Repeated polling
                poll_task = asyncio.create_task(
                    poll_loop(client, base, args.show_interval, args.show_slots, args.show_models, args.raw)
                )
            elif args.show_delay is not None:
                # Single display after delay
                await asyncio.sleep(args.show_delay)
                await show_once(client, base, args.show_slots, args.show_models, args.raw)
            else:
                # Immediate one-shot
                await show_once(client, base, args.show_slots, args.show_models, args.raw)

        if tasks:
            await asyncio.gather(*tasks)
        elif poll_task:
            # No inference — let polling run until ctrl-c
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        if poll_task:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

    if n > 0:
        print("\nAll requests complete.")


if __name__ == "__main__":
    asyncio.run(main())
