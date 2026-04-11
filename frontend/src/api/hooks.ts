import { useState, useEffect, useRef, useCallback, useTransition } from "react";
import { api } from "./client";
import { getWsV2 } from "./wsv2";
import type {
  ServerConfig,
  ServerStatus,
  ProxyStatus,
  HealthStatus,
  SlotInfo,
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

export function useProxyStatus(pollMs = 5000) {
  const [status, setStatus] = useState<ProxyStatus>({
    state: "unknown",
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

export type LogLine = { id: string; line_number: number; text: string; request_id?: string };

export function useLogs(type: "proxy" | "server", serverId?: string) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);
  const [isPending, startTransition] = useTransition();
  const maxIdRef = useRef(0);

  useEffect(() => {
    if (type === "server" && !serverId) return;

    const wsv2 = getWsV2();
    maxIdRef.current = 0;

    const onConnect = () => {
      setConnected(true);
      setLines([]);
      maxIdRef.current = 0;
      wsv2.send({ msg: "load_log", type, ...(serverId ? { suid: serverId } : {}) });
    };

    const handleLoad = (msg: Record<string, unknown>) => {
      if (msg.type !== type) return;
      if (type === "server" && msg.suid !== serverId) return;
      const loaded = ((msg.lines as Array<{ id: string; line_number: number; text: string; request_id?: string }>) ?? []).map((l) => {
        const line: LogLine = { id: l.id, line_number: l.line_number, text: l.text };
        if (l.request_id) line.request_id = l.request_id;
        return line;
      });
      maxIdRef.current = loaded.reduce((m, l) => Math.max(m, l.line_number), 0);
      startTransition(() => setLines(loaded));
    };

    const handleEvent = (data: Record<string, unknown>) => {
      const lineNumber = data.line_number as number;
      if (lineNumber <= maxIdRef.current) return;
      maxIdRef.current = lineNumber;
      const line: LogLine = { id: data.line_id as string, line_number: lineNumber, text: data.text as string };
      if (data.request_id) line.request_id = data.request_id as string;
      startTransition(() => setLines((prev) => [...prev, line]));
    };

    const unsubLoad = wsv2.subscribe("load_log_response", handleLoad, onConnect);
    const unsubEvent = wsv2.subscribeToEvent("log", type === "server" ? serverId! : null, handleEvent, type);

    return () => {
      setConnected(false);
      setLines([]);
      unsubLoad();
      unsubEvent();
    };
  }, [type, serverId]);

  const clear = useCallback(() => setLines([]), []);

  return { lines, connected, clear, isPending };
}

export function useRemotes(pollMs = 3000) {
  const [remotes, setRemotes] = useState<RemoteManagerStatus[]>([]);

  const handleMessage = useCallback((msg: Record<string, unknown>) => {
    setRemotes((msg.remotes ?? []) as RemoteManagerStatus[]);
  }, []);

  const refresh = useCallback(() => getWsV2().send({ msg: "remotes" }), []);

  useEffect(() => {
    const unsub = getWsV2().subscribe("remotes_response", handleMessage, refresh);
    const id = setInterval(refresh, pollMs);
    return () => {
      unsub();
      clearInterval(id);
    };
  }, [handleMessage, refresh, pollMs]);

  return remotes;
}

// ---------------------------------------------------------------------------
// Health and slot streams via ws_v2 subscribeToEvent
// ---------------------------------------------------------------------------

export function useSlotStream(suid: string | undefined): SlotInfo[] {
  const [slots, setSlots] = useState<SlotInfo[]>([]);

  useEffect(() => {
    if (!suid) return;
    return getWsV2().subscribeToEvent("slots", suid, (data) => {
      setSlots((data.slots as SlotInfo[]) ?? []);
    });
  }, [suid]);

  return slots;
}

const HEALTH_STALE_MS = 10_000;

export function useHealthStream(suid: string | undefined): HealthStatus | null {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!suid) return;
    const unsub = getWsV2().subscribeToEvent("health", suid, (data) => {
      const h = data.health as HealthStatus | undefined;
      if (!h) return;
      setHealth(h);
      if (staleTimer.current) clearTimeout(staleTimer.current);
      staleTimer.current = setTimeout(() => setHealth(null), HEALTH_STALE_MS);
    });
    return () => {
      unsub();
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
  }, [suid]);

  return health;
}

export function useProxyStatusWS() {
  const [status, setStatus] = useState<ProxyStatus>({
    state: "unknown",
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
      if (!serverId || msg.suid !== serverId) return;
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
    if (serverId) getWsV2().send({ msg: "server_status", suid: serverId });
  }, [serverId]);

  useEffect(
    () => getWsV2().subscribe("server_status_response", handleStatusResponse, refresh),
    [handleStatusResponse, refresh],
  );

  useEffect(() => {
    if (!serverId) return;
    return getWsV2().subscribeToEvent("server_status", serverId, (data) => {
      const state = data.state as ServerStatus["state"];
      if (state === "running") {
        if (startedAtRef.current == null) {
          refresh();
        }
        setStatus((prev) => prev ? { ...prev, state } : null);
      } else {
        startedAtRef.current = null;
        setStatus((prev) => prev ? { ...prev, state, uptime: null, pid: null } : null);
      }
    });
  }, [serverId, refresh]);

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


export function useUplinkStatus(pollMs = 3000) {
  const [uplink, setUplink] = useState<UplinkStatus | null>(null);

  const handleMessage = useCallback((msg: Record<string, unknown>) => {
    setUplink(msg as unknown as UplinkStatus);
  }, []);

  const refresh = useCallback(() => getWsV2().send({ msg: "uplink_status" }), []);

  useEffect(() => {
    const unsub = getWsV2().subscribe("uplink_status_response", handleMessage, refresh);
    const id = setInterval(refresh, pollMs);
    return () => {
      unsub();
      clearInterval(id);
    };
  }, [handleMessage, refresh, pollMs]);

  return uplink;
}
