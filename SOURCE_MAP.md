# SOURCE_MAP.md

Quick-reference map of every module in `src/llama_manager/` and `frontend/src/` for agent priming.

---

## Python Backend — `src/llama_manager/`

### Top-level

| File | Purpose | Key exports |
|------|---------|-------------|
| `__init__.py` | Package init | version `0.4.0` |
| `__main__.py` | CLI entry point | argparse: `--dev`, `--verbose`, `--debug`; starts uvicorn |
| `config.py` | Pydantic v2 config models; loads/saves `server_config.json` | `AppConfig`, `ModelConfig`, `ModelAdvanced`, `WebUIConfig`, `ApiServerConfig`, `RemoteManagerConfig`, `ManagerUplinkConfig`, `load_config()`, `save_config()` |
| `main.py` | FastAPI app + lifespan; mounts routers; serves frontend SPA | `app` (FastAPI), `lifespan` |
| `process_manager.py` | Subprocess lifecycle for one llama-server instance; state machine; log streaming | `ProcessManager` (`start`, `stop`, `restart`, `get_status`, `subscribe`, `unsubscribe`) |
| `log_buffer.py` | Thread-safe circular log buffer with monotonic IDs | `LogBuffer`, `LogLine` |
| `llama_client.py` | HTTP client for llama-server endpoints (`/health`, `/slots`, `/props`) | `LlamaClient` |
| `remote_manager_client.py` | WebSocket client to a remote Llama Manager; duck-types ProcessManager for remote models | `RemoteManagerClient`, `RemoteModelProxy` |

### `kv_cache/`

| File | Purpose | Key exports |
|------|---------|-------------|
| `__init__.py` | Re-exports | `KVCache`, `KVCacheProvider`, `CacheHit`, `CacheMiss`, `CacheInvalid` |
| `cache.py` | JSON-file-backed KV state cache keyed by conversation hash | `KVCache`, `KVCacheProvider` (singleton), `CacheValid`, `CacheHit`, `CacheMiss`, `CacheInvalid` |
| `messages.py` | Conversation hash and cacheability validation | `is_cacheable()`, `conversation_hash()` |
| `path.py` | Resolves `slot_save_path` from config | `resolve_slot_save_path()` |
| `slots.py` | Async-safe slot reservation tracker | `SlotAvailability`, `SlotAvailabilityProvider` (singleton) |

### `proxy/`

| File | Purpose | Key exports |
|------|---------|-------------|
| `__init__.py` | Re-exports | `proxy_app`, `start_proxy`, `stop_proxy`, `restart_proxy`, `get_proxy_status` |
| `proxy.py` | Proxy FastAPI app; request-ID middleware; lifecycle functions | `proxy_app`, `start_proxy()`, `stop_proxy()`, `restart_proxy()`, `get_proxy_status()` |
| `openai.py` | OpenAI-compatible endpoint handler; stream passthrough; KV cache integration | `openai_proxy()`, `_stream_passthrough()` |
| `translate.py` | Message normalization for llama-server compatibility | `normalize_messages()` |
| `models.py` | Model resolution: ID → index → backend URL | `resolve_model_index()`, `resolve_backend()`, `default_backend()`, `rewrite_model_field()`, `backend_error_msg()` |
| `lifecycle.py` | JIT model start; TTL idle shutdown background task | `ensure_model_server()`, `touch_model()`, `_ttl_checker()` |
| `active_requests.py` | Registry of in-flight requests; supports cancellation via asyncio.Event | `ActiveRequestManager` (`register`, `unregister`, `cancel`, `list_cancellable`) |
| `logging.py` | Structured proxy request/response logging (→ / ← arrows) | `log_req()`, `log_resp()`, `log_stream_end()` |
| `request_log.py` | Rotating in-memory log of recent proxy requests | `RequestLog`, `RequestLogEntry`, `request_log` (global) |
| `subscription.py` | Proxy log buffer + pub/sub for WebSocket streaming | `proxy_log_buffer`, `proxy_log()`, `proxy_subscribe()`, `proxy_unsubscribe()` |
| `slots.py` | Low-level slot save/restore HTTP calls to llama-server | `slot_save()`, `slot_restore()` |

### `routers/`

| File | Purpose | Key exports |
|------|---------|-------------|
| `server.py` | APIRouter `/api/server` — declares all server/proxy/config routes | router |
| `status.py` | APIRouter `/api/status` — health, slots, props, requests routes | router |
| `ws.py` | WebSocket endpoints: `/ws/logs` (log streaming) and `/ws/manager` (uplink federation) | router |
| `remotes.py` | GET `/api/remotes`, `/api/remotes/uplink` — lists connected remote managers | router |

### `routers/routes/`

| File | Purpose |
|------|---------|
| `server.py` | `get_status`, `start`, `stop`, `restart` — delegate to ProcessManager or RemoteModelProxy |
| `config.py` | `get_config`, `put_config` — load/save config, sync process managers, sync remote clients |
| `health.py` | `get_health` — LlamaClient or cached RemoteModelProxy health |
| `slots.py` | `get_slots`, `cancel_slot` — slot data with prompt_progress overlay |
| `props.py` | `get_props` — forwards `/props` from llama-server |
| `requests.py` | `list_requests`, `get_request` — proxy request log access |
| `proxy.py` | `proxy_status`, `proxy_start`, `proxy_stop`, `proxy_restart` — proxy lifecycle wrappers |

---

## React Frontend — `frontend/src/`

### Entry & Routing

| File | Purpose |
|------|---------|
| `main.tsx` | React 19 root render |
| `App.tsx` | BrowserRouter + routes: `/`, `/:modelIndex/properties`, `/:modelIndex/slots`, `/logs/:source?`, `/settings` |

### API Layer — `api/`

| File | Purpose | Key exports |
|------|---------|-------------|
| `types.ts` | TypeScript interfaces mirroring all backend response shapes | `ServerStatus`, `ServerConfig`, `ModelConfig`, `HealthStatus`, `SlotInfo`, `ProxyStatus`, `RequestLogEntry`, `RemoteManagerStatus`, `UplinkStatus`, … |
| `client.ts` | Generic fetch wrapper + all endpoint functions | `api` object (`getStatus`, `start`, `stop`, `restart`, `putConfig`, `getHealth`, `getSlots`, `cancelSlot`, …), `wsUrl()` |
| `hooks.ts` | React polling and WebSocket hooks | `useServerStatus()`, `useProxyStatus()`, `useHealth()`, `useSlots()`, `useProps()`, `useLogs()`, `useRemotes()`, `useSlotStream()`, `useHealthStream()`, `useUplinkStatus()` |

### Pages — `pages/`

| File | Purpose |
|------|---------|
| `Dashboard.tsx` | Home — one `ModelPanel`/`RemoteModelPanel` per model; shows status, controls, health, slots |
| `Logs.tsx` | Log viewer with source selector (`ScrollStrip`), WebSocket log streaming per model/proxy |
| `Properties.tsx` | Shows `/api/status/props` JSON for selected model (`JsonTree`) |
| `Slots.tsx` | Slots table for selected model; cancel button on busy slots |
| `Settings.tsx` | Config editor with tabs (General, Proxy, Model-N, Remote-N); add/delete model/remote |

### Components — `components/`

| File | Purpose |
|------|---------|
| `Layout.tsx` | App shell with sidebar nav (Dashboard, Logs, Settings) |
| `ServerStatusCard.tsx` | Model state card — color-coded state, PID, uptime, slot grid with tooltips |
| `ServerControls.tsx` | Start/Stop/Restart buttons for a model |
| `ProxyStatusCard.tsx` | Proxy state card — host:port, state, uptime, PID |
| `ProxyControls.tsx` | Start/Stop/Restart buttons for proxy |
| `HealthCard.tsx` | Dashboard health summary using ✓/✗/•/– characters; shows proxy + uplink + per-model status |
| `LogViewer.tsx` | Log display with tab filters (Server/Info/Other/All), request_id click-through to detail modal |
| `ConfigEditor.tsx` | Form for `ModelConfig` or `RemoteManagerConfig`; save/delete; advanced section |
| `config-defaults.ts` | `defaultConfig`, `defaultRemoteManager` templates; `SettingsTab` type |
| `JsonTree.tsx` | Expandable color-coded JSON tree |
| `TabBar.tsx` | `TabButton` component + `PANEL_BG` shared constant |
| `RequestDetail.tsx` | Modal overlay — two-pane request/response body viewer |

---

## Key Cross-Cutting Flows

| Flow | Backend path | Frontend path |
|------|-------------|---------------|
| Server status polling | `ProcessManager.get_status()` → `GET /api/server/status?model=N` | `useServerStatus()` → `ServerStatusCard` |
| Log streaming | `LogBuffer` → pub/sub → `WS /ws/logs?source=model-N` | `useLogs()` → `LogViewer` |
| Proxy request | `openai_proxy()` → KVCache → slot alloc → llama-server | n/a (external callers) |
| Config change | `PUT /api/server/config` → `save_config()` + PM sync | `ConfigEditor` → `api.putConfig()` |
| Remote federation | `RemoteManagerClient` (WS client) ↔ `RemoteModelProxy` (duck-typed PM) | `useRemotes()` → Dashboard remote panels |
| JIT model start | `ensure_model_server()` in `proxy/lifecycle.py` | — |
| Slot cancel | `active_requests.cancel()` → `POST /api/status/slots/cancel` | `SlotsTable` cancel button → `api.cancelSlot()` |
