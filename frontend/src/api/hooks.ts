import { useState, useEffect, useRef, useCallback } from "react";
import { api, wsUrl } from "./client";
import { getWsV2 } from "./wsv2";
import type {
  ServerConfig,
  ServerStatus,
  ProxyStatus,
  HealthStatus,
  SlotInfo,
  ModelProps,
  LogMessage,
  RemoteManagerStatus,
  UplinkStatus,
} from "./types";

export function pollRatesFromConfig(cfg: ServerConfig | null) {
  const w = cfg?.web_ui;
  return {
    serverStatus: w?.poll_server_status ?? undefined,
    proxyStatus: w?.poll_proxy_status ?? undefined,
    health: w?.poll_health ?? undefined,
    slots: w?.poll_slots ?? undefined,
    slotsActive: w?.poll_slots_active ?? undefined,
  };
}

export function useServerStatus(modelIndex = 0, pollMs = 3000) {
  const [status, setStatus] = useState<ServerStatus>({
    state: "stopped",
    pid: null,
    host: null,
    port: null,
    uptime: null,
  });

  const poll = useCallback(() => {
    api
      .getStatus(modelIndex)
      .then(setStatus)
      .catch(() => {});
  }, [modelIndex]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, pollMs);
    return () => clearInterval(id);
  }, [poll, pollMs]);

  return { status, refresh: poll };
}

export function useProxyStatus(pollMs = 5000) {
  const [status, setStatus] = useState<ProxyStatus>({
    state: "stopped",
    host: null,
    port: null,
    uptime: null,
    pid: null,
  });

  const poll = useCallback(() => {
    api
      .getProxyStatus()
      .then(setStatus)
      .catch(() => {});
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, pollMs);
    return () => clearInterval(id);
  }, [poll, pollMs]);

  return { status, refresh: poll };
}

export function useHealth(modelIndex = 0, pollMs = 5000) {
  const [health, setHealth] = useState<HealthStatus | null>(null);

  useEffect(() => {
    const fetch = () => {
      api
        .getHealth(modelIndex)
        .then(setHealth)
        .catch(() => setHealth(null));
    };
    fetch();
    const id = setInterval(fetch, pollMs);
    return () => clearInterval(id);
  }, [modelIndex, pollMs]);

  return health;
}

export function useSlots(
  modelIndex = 0,
  pollMs = 5000,
  activePollMs = 500,
  serverState?: string,
) {
  const [slots, setSlots] = useState<SlotInfo[]>([]);
  const hasActive = slots.some((s) => s.is_processing);
  const effectiveMs = hasActive ? activePollMs : pollMs;
  const active = serverState === "running" || serverState === "remote";

  useEffect(() => {
    if (!active) return;
    const fetch = () => {
      api
        .getSlots(modelIndex)
        .then(setSlots)
        .catch(() => setSlots([]));
    };
    fetch();
    const id = setInterval(fetch, effectiveMs);
    return () => {
      clearInterval(id);
    };
  }, [modelIndex, effectiveMs, active]);

  // Clear slots only when the server is no longer active
  useEffect(() => {
    if (!active) setSlots([]);
  }, [active]);

  return slots;
}

export function useProps() {
  const [props, setProps] = useState<ModelProps | null>(null);

  useEffect(() => {
    api
      .getProps()
      .then(setProps)
      .catch(() => setProps(null));
  }, []);

  return props;
}

export type LogLine = { id: number; text: string; request_id?: string };

export function useLogs(source = "model-0") {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const [serverState, setServerState] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(wsUrl(source));
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) setTimeout(connect, 2000);
      };
      ws.onmessage = (ev) => {
        const msg: LogMessage = JSON.parse(ev.data);
        if (msg.type === "log" && msg.id != null && msg.text != null) {
          const line: LogLine = { id: msg.id!, text: msg.text! };
          if (msg.request_id) line.request_id = msg.request_id;
          setLines((prev) => [...prev, line]);
        } else if (msg.type === "state" && msg.state) {
          setServerState(msg.state);
          if (msg.state === "starting") {
            setLines([]);
          }
        }
      };
    }

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
      setLines([]);
    };
  }, [source]);

  const clear = useCallback(() => setLines([]), []);

  return { lines, connected, serverState, clear };
}

export function useRemotes(pollMs = 3000) {
  const [remotes, setRemotes] = useState<RemoteManagerStatus[]>([]);

  useEffect(() => {
    const fetch = () => {
      api.getRemotes().then(setRemotes).catch(() => {});
    };
    fetch();
    const id = setInterval(fetch, pollMs);
    return () => clearInterval(id);
  }, [pollMs]);

  return remotes;
}

// ---------------------------------------------------------------------------
// Event stream — singleton WebSocket to /ws/events
// ---------------------------------------------------------------------------

type SlotEventHandler = (serverId: string, slots: SlotInfo[]) => void;
type HealthEventHandler = (serverId: string, health: HealthStatus) => void;

let _eventWs: WebSocket | null = null;
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
const _slotHandlers = new Set<SlotEventHandler>();
const _healthHandlers = new Set<HealthEventHandler>();

function _hasListeners() {
  return _slotHandlers.size > 0 || _healthHandlers.size > 0;
}

function _connectEventStream() {
  if (_eventWs?.readyState === WebSocket.OPEN || _eventWs?.readyState === WebSocket.CONNECTING) return;
  if (_reconnectTimer) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  _eventWs = new WebSocket(`${proto}//${location.host}/ws/events`);
  _eventWs.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "slots" && msg.server_id) {
        _slotHandlers.forEach((h) => h(msg.server_id, msg.slots ?? []));
      } else if (msg.type === "health" && msg.server_id) {
        _healthHandlers.forEach((h) => h(msg.server_id, msg.health));
      }
    } catch {}
  };
  _eventWs.onclose = () => {
    _eventWs = null;
    if (_hasListeners()) {
      _reconnectTimer = setTimeout(() => {
        _reconnectTimer = null;
        _connectEventStream();
      }, 2000);
    }
  };
}

export function useSlotStream(serverId: string | undefined): SlotInfo[] {
  const [slots, setSlots] = useState<SlotInfo[]>([]);

  useEffect(() => {
    if (!serverId) return;
    const handler: SlotEventHandler = (id, data) => {
      if (id === serverId) setSlots(data);
    };
    _slotHandlers.add(handler);
    _connectEventStream();
    return () => {
      _slotHandlers.delete(handler);
    };
  }, [serverId]);

  return slots;
}

const HEALTH_STALE_MS = 10_000;

export function useHealthStream(serverId: string | undefined): HealthStatus | null {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!serverId) return;
    const handler: HealthEventHandler = (id, data) => {
      if (id !== serverId) return;
      setHealth(data);
      if (staleTimer.current) clearTimeout(staleTimer.current);
      staleTimer.current = setTimeout(() => setHealth(null), HEALTH_STALE_MS);
    };
    _healthHandlers.add(handler);
    _connectEventStream();
    return () => {
      _healthHandlers.delete(handler);
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
  }, [serverId]);

  return health;
}

export function useProxyStatusWS() {
  const [status, setStatus] = useState<ProxyStatus>({
    state: "stopped",
    host: null,
    port: null,
    uptime: null,
    pid: null,
  });

  // Tracks the local epoch (ms) when the proxy started, derived from server uptime.
  // Used to tick uptime locally without re-fetching.
  const startedAtRef = useRef<number | null>(null);

  const handleMessage = useCallback((msg: Record<string, unknown>) => {
    const s = msg as unknown as ProxyStatus;
    setStatus(s);
    startedAtRef.current =
      s.state === "running" && s.uptime != null
        ? Date.now() - s.uptime * 1000
        : null;
  }, []);

  const refresh = useCallback(() => getWsV2().send({ msg: "proxy_status" }), []);

  useEffect(() => {
    return getWsV2().subscribe("proxy_status_response", handleMessage, refresh);
  }, [handleMessage, refresh]);

  // Tick uptime every second from the local anchor, no server round-trip needed.
  useEffect(() => {
    const id = setInterval(() => {
      if (startedAtRef.current != null) {
        setStatus((prev) => ({
          ...prev,
          uptime: (Date.now() - startedAtRef.current!) / 1000,
        }));
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  return { status, refresh };
}

export function useServerStatusWS(serverId: string | undefined) {
  const [status, setStatus] = useState<ServerStatus | null>(null);

  const startedAtRef = useRef<number | null>(null);

  const handleStatusResponse = useCallback(
    (msg: Record<string, unknown>) => {
      if (!serverId || msg.id !== serverId) return;
      const s = msg as unknown as ServerStatus;
      setStatus(s);
      startedAtRef.current =
        s.state === "running" && s.uptime != null
          ? Date.now() - s.uptime * 1000
          : null;
    },
    [serverId],
  );

  const refresh = useCallback(() => {
    if (serverId) getWsV2().send({ msg: "server_status", id: serverId });
  }, [serverId]);

  useEffect(
    () => getWsV2().subscribe("server_status_response", handleStatusResponse, refresh),
    [handleStatusResponse, refresh],
  );

  useEffect(() => {
    if (!serverId) return;
    return getWsV2().subscribeToEvent("server_status", serverId, (data) => {
      const state = data.state as ServerStatus["state"];
      setStatus((prev) => prev ? { ...prev, state } : null);
      if (state !== "running" && state !== "remote") {
        startedAtRef.current = null;
      }
    });
  }, [serverId]);

  useEffect(() => {
    const id = setInterval(() => {
      if (startedAtRef.current != null) {
        setStatus((prev) => prev ? ({
          ...prev,
          uptime: (Date.now() - startedAtRef.current!) / 1000,
        }) : null);
      }
    }, 1000);
    return () => clearInterval(id);
  }, []);

  return { status, refresh };
}

export function useSlotStatusWS(
  modelIndex = 0,
  serverState?: string,
  pollMs = 5000,
  activePollMs = 500,
) {
  const [slots, setSlots] = useState<SlotInfo[]>([]);
  const hasActive = slots.some((s) => s.is_processing);
  const effectiveMs = hasActive ? activePollMs : pollMs;
  const active = serverState === "running" || serverState === "remote";

  const handleMessage = useCallback(
    (msg: Record<string, unknown>) => {
      if ((msg.model as number) !== modelIndex) return;
      setSlots((msg.slots as SlotInfo[]) ?? []);
    },
    [modelIndex],
  );

  const refresh = useCallback(
    () => getWsV2().send({ msg: "slot_status", model: modelIndex }),
    [modelIndex],
  );

  useEffect(() => {
    return getWsV2().subscribe("slot_status_response", handleMessage, refresh);
  }, [handleMessage, refresh]);

  useEffect(() => {
    if (!active) {
      setSlots([]);
      return;
    }
    const id = setInterval(refresh, effectiveMs);
    return () => clearInterval(id);
  }, [refresh, effectiveMs, active]);

  return slots;
}

export function useUplinkStatus(pollMs = 3000) {
  const [uplink, setUplink] = useState<UplinkStatus | null>(null);

  useEffect(() => {
    const fetch = () => {
      api.getUplinkStatus().then(setUplink).catch(() => {});
    };
    fetch();
    const id = setInterval(fetch, pollMs);
    return () => clearInterval(id);
  }, [pollMs]);

  return uplink;
}
