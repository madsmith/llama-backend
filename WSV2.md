# WSv2 — Bi-directional UI WebSocket

Endpoint: `GET /v2/ws/manager` (upgraded to WebSocket)

All messages are JSON objects with a `msg` field that acts as the discriminator.
The client sends requests; the server responds immediately and may also push
unsolicited events in the future.

---

## Protocol shape

```
client → server   { "msg": "<type>", ...payload }
server → client   { "msg": "<type>_response", ...payload }
```

---

## Adding a new message pair — touch points

### 1. Backend — `src/llama_manager/protocol/ws_messages.py`

Add the request model to the incoming section and the response model to the
outgoing section.

```python
# Incoming
class FooRequest(BaseModel):
    msg: Literal["foo"] = "foo"
    # optional request fields here

# Outgoing
class FooResponse(BaseModel):
    msg: Literal["foo_response"] = "foo_response"
    # response fields here
```

Then extend the `IncomingMessage` union so Pydantic's discriminated-union
dispatch picks it up:

```python
IncomingMessage = Annotated[
    Union[ProxyStatusRequest, FooRequest],   # ← add here
    Field(discriminator="msg"),
]
```

### 2. Backend — `src/llama_manager/routers/ws_v2.py`

Import the new types and add a branch to `_dispatch`. The return type union
must also include the new response type.

```python
from llama_manager.protocol.ws_messages import (
    ...
    FooRequest,
    FooResponse,
)

def _dispatch(
    msg: IncomingMessage, proxy: ProxyServer
) -> ProxyStatusResponse | FooResponse | None:   # ← extend return type
    if isinstance(msg, ProxyStatusRequest):
        return ProxyStatusResponse(**proxy.status())
    if isinstance(msg, FooRequest):              # ← add branch
        return FooResponse(...)
    return None
```

If the handler needs access to `app.state` (e.g. process managers), add it as
a parameter to `_dispatch` and pass it from `ui_ws`:

```python
response = _dispatch(msg, proxy, ws.app.state)
```

### 3. Frontend — `frontend/src/api/wsv2.ts`

No changes required for simple request/response pairs — the client dispatches
by `msg` type automatically.

If the new message type is a **server-pushed event** (not a response to a
request), no frontend wiring is needed beyond the hook.

### 4. Frontend — `frontend/src/api/hooks.ts`

Add a hook that subscribes to the response type and sends the request via
`onConnect` (so it re-fires on reconnect automatically).

```typescript
export function useFooWS() {
  const [data, setData] = useState<FooData | null>(null);

  const refresh = useCallback(
    () => getWsV2().send({ msg: "foo" }),
    [],
  );

  useEffect(() => {
    return getWsV2().subscribe(
      "foo_response",
      (msg) => setData(msg as unknown as FooData),
      refresh,   // called on connect and each reconnect
    );
  }, [refresh]);

  return { data, refresh };
}
```

### 5. Frontend — component

Replace the REST-polling hook with the new WS hook:

```tsx
// before
const { status, refresh } = useProxyStatus(poll.proxyStatus);

// after
const { status, refresh } = useFooWS();
```

---

## Server-pushed events (no request needed)

For state that the server pushes proactively (e.g. slot updates), skip steps
1–3 for the request half and only define the outgoing message type. On the
backend, publish from wherever the state change originates:

```python
await ws.send_text(FooEvent(...).model_dump_json())
```

The frontend hook subscribes to the event type with no `onConnect` send.

---

## Current messages

| `msg` (client → server) | `msg` (server → client) | Description |
|---|---|---|
| `proxy_status` | `proxy_status_response` | Proxy server state, host, port, uptime, pid |
| `server_status` (+ `model`) | `server_status_response` | Model server state, host, port, uptime, pid |
