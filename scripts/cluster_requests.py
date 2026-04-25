#!/usr/bin/env python3
"""
Iteratively cluster request log files by shared message prefix.

Starting from an input folder, compute a hash of the first N messages for
every .json file. Files whose hash appears >= MIN_COUNT times are copied into
<output>/1/. Then repeat with those files at depth N+1, copying frequent ones
into <output>/2/, and so on until no hash meets the threshold.
"""

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


def load_messages(path: Path) -> list:
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return []
    return (data.get("request_body") or {}).get("messages", [])


def hash_messages(messages: list, depth: int) -> str:
    subset = messages[:depth]
    canonical = json.dumps(subset, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def process_level(src: Path, dst: Path, depth: int, min_count: int) -> int:
    """
    Hash every .json in src at the given depth, copy files whose hash
    appears >= min_count times into dst. Returns number of files copied.
    """
    files = list(src.glob("req*.json"))
    if not files:
        return 0

    hashes: dict[Path, str] = {}
    for f in files:
        msgs = load_messages(f)
        if len(msgs) < depth:
            continue
        hashes[f] = hash_messages(msgs, depth)

    counts = Counter(hashes.values())
    frequent = {h for h, n in counts.items() if n >= min_count}

    if not frequent:
        return 0

    dst.mkdir(parents=True, exist_ok=True)
    index: dict[str, list[str]] = {}
    copied = 0
    for f, h in hashes.items():
        if h in frequent:
            shutil.copy2(f, dst / f.name)
            index.setdefault(h, []).append(f.name)
            copied += 1

    with open(dst / "index.json", "w") as fh:
        json.dump(index, fh, indent=2)

    return copied


def main():
    parser = argparse.ArgumentParser(
        description="Cluster request logs by shared message prefix depth."
    )
    parser.add_argument("input_folder", help="Folder containing source .json log files")
    parser.add_argument("output_folder", help="Root output folder (levels written to <output>/1/, /2/, ...)")
    parser.add_argument(
        "--min-count",
        type=int,
        default=10,
        metavar="N",
        help="Minimum hash occurrences to keep a file (default: 10)",
    )
    args = parser.parse_args()

    src = Path(args.input_folder)
    out = Path(args.output_folder)
    min_count = args.min_count

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    depth = 1
    current_src = src

    while True:
        dst = out / str(depth)
        print(f"Depth {depth}: scanning {current_src} ...", end=" ", flush=True)
        copied = process_level(current_src, dst, depth, min_count)
        print(f"{copied} files copied to {dst}")

        if copied == 0:
            print("No hashes met the threshold — done.")
            break

        current_src = dst
        depth += 1


if __name__ == "__main__":
    main()
