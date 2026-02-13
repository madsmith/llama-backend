# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Llama Server Manager — a web UI + API for managing multiple llama-server processes. Two servers run simultaneously: a management app (default port 8000) and an OpenAI/Anthropic-compatible proxy (configured port, default 1234).

## Commands

### Running

```bash
# Dev mode (Vite HMR + backend reload)
llama-manager --dev

# Production
llama-manager

# With logging
llama-manager --verbose    # HTTP requests
llama-manager --debug      # Full debug
```

The management app runs on port 8000. The proxy starts automatically on the port configured in `server_config.json` (default 1234).

### Frontend

```bash
cd frontend
pnpm dev          # Vite dev server (proxies /api and /ws to localhost:8000)
pnpm build        # Production build
pnpm lint         # ESLint
npx tsc --noEmit  # Type-check
```

### Python syntax check (no venv needed)

```bash
python3 -m py_compile src/main.py
```

## Architecture

### Dual-Server Design

**Management app** (`src/main.py`, port 8000): FastAPI app serving the React frontend and management API (`/api/server/*`, `/api/status/*`, `/ws/logs`). In production, serves built frontend from `frontend/dist/` with SPA catch-all.

**Proxy app** (`src/proxy.py`, configured port): Separate FastAPI app started inside the management app's lifespan. Exposes OpenAI (`/v1/chat/completions`, `/v1/models`) and Anthropic (`/v1/messages`) endpoints, routing requests to the correct llama-server by model ID. Translates Anthropic format to OpenAI format internally.

### Multi-Model Process Management

Each configured model gets its own `ProcessManager` instance (`src/process_manager.py`) and its own llama-server port (`starting_port + model_index`). Process managers are stored as `app.state.process_managers: list[ProcessManager]`.

All management API endpoints accept `?model=N` query param (default 0). WebSocket logs use `?source=model-0`, `model-1`, or `proxy`.

**State machine:** `stopped → starting → running → stopping → stopped` (plus `error` terminal state). The "starting → running" transition happens when "listening" appears in llama-server stdout.

**JIT start:** When `jit-model-server` is enabled, the proxy auto-starts the target model's server on incoming requests and waits up to `jit-timeout` seconds.

### Config System

`src/config.py` — Pydantic v2 models with kebab-case aliases (`Field(alias="llama-server-path")`). Config lives at `server_config.json` in the project root, auto-created with defaults if missing. `load_config()` is called fresh on each use (not cached).

When models are added/removed via the config PUT endpoint, the process managers list is synced to match.

### Frontend Patterns

- **React 19 + React Router v7 + Tailwind CSS 4 + Vite 7**
- **Hooks with polling:** `useServerStatus(modelIndex, pollMs)`, `useSlots(modelIndex)`, `useHealth(modelIndex)` poll REST endpoints. `useLogs(source)` uses WebSocket with auto-reconnect.
- **Per-model components:** Dashboard and Logs pages render a component per model (each with its own hooks), since React hooks can't be called in loops. Status/health data flows up via callbacks.
- **Settings tabs:** Tab IDs are `"manager"`, `"proxy"`, `"model-0"`, `"model-1"`, etc. ConfigEditor receives `modelIndex` prop and uses `config.models[modelIndex]`. Model 0 cannot be deleted.

### Log Streaming

Each `ProcessManager` and the proxy have their own `LogBuffer` (circular, thread-safe) and pub/sub system via `asyncio.Queue`. The WebSocket endpoint (`/ws/logs`) sends buffered history then subscribes for live updates. Messages are `{"type": "log"|"state", ...}`.
