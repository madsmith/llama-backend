from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Llama Server Manager")
    parser.add_argument(
        "--dev", action="store_true",
        help="Dev mode: start Vite HMR server alongside the backend",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Verbose logging for process management",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.dev:
        os.environ["LLAMA_DEV"] = "1"
    if args.debug:
        os.environ["LLAMA_DEBUG"] = "1"
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        reload_dirs=["src"] if args.dev else None,
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
