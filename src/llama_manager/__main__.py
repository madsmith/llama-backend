from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Llama Server Manager")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: start Vite HMR server alongside the backend",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show HTTP requests and server activity",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Full debug logging (implies --verbose)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--save-log",
        action="store_true",
        help="Write each proxy request to logs/<request_id>.json",
    )
    args = parser.parse_args()

    if args.debug:
        args.verbose = True

    if args.save_log:
        os.environ["LLAMA_SAVE_LOGS"] = "1"
    if args.dev:
        os.environ["LLAMA_DEV"] = "1"
    if args.debug:
        os.environ["LLAMA_DEBUG"] = "1"
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        os.environ["LLAMA_VERBOSE"] = "1"
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.debug:
        log_level = "debug"
    elif args.verbose:
        log_level = "info"
    else:
        log_level = "warning"

    if not args.dev:
        print(f"[frontend] http://{args.host}:{args.port}")

    import uvicorn

    uvicorn.run(
        "llama_manager.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        reload_dirs=["src/llama_manager"] if args.dev else None,
        reload_delay=5.0 if args.dev else None,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
