#!/usr/bin/env bash
# Build llama-manager for running as a service: Python venv with the package
# installed (editable, so config and frontend/dist resolve inside this repo)
# and a production frontend bundle.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: '$1' not found in PATH" >&2; exit 1; }; }
need uv
need pnpm

echo "==> Syncing Python environment (.venv)"
uv sync

echo "==> Building frontend (frontend/dist)"
(cd frontend && pnpm install --frozen-lockfile && pnpm build)

echo "==> Build complete: $ROOT/.venv/bin/llama-manager"
