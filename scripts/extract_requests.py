#!/usr/bin/env python3
"""Extract or hash the first N messages from a llama-manager logged request file."""

import argparse
import hashlib
import json
import sys


def load_messages(path: str) -> list:
    with open(path) as f:
        data = json.load(f)
    return (data.get("request_body") or {}).get("messages", [])


def main():
    parser = argparse.ArgumentParser(
        description="Extract messages from a llama-manager logged request file."
    )
    parser.add_argument("request_file", help="Path to the request JSON log file")
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        metavar="N",
        help="Number of messages to output (default: 1)",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help="Instead of printing messages, output a SHA-256 hash of the first --depth messages",
    )
    args = parser.parse_args()

    messages = load_messages(args.request_file)
    subset = messages[: args.depth]

    if args.hash:
        canonical = json.dumps(subset, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        print(digest)
    else:
        print(json.dumps(subset, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
