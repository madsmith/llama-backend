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

export type WireTextLog = { type: "text"; text: string };
export type WireProxyRequest = { type: "request"; method: string; path: string; http_ver: string; size?: number | null; server_name?: string | null };
export type WireProxyResponse = { type: "response"; status: number; phrase: string; http_ver: string; streaming: boolean; complete: boolean; elapsed?: number | null; size?: number | null; server_name?: string | null };
export type WireLogData = WireTextLog | WireProxyRequest | WireProxyResponse;
export type LogLine = { id: string; line_number: number; time: number; request_id?: string | null; data: WireLogData };

const LOG_PAGE_SIZE = 200;

export function useLogs(type: "proxy" | "server", serverId?: string) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isPending, startTransition] = useTransition();
  const maxIdRef = useRef(0);
  const linesRef = useRef<LogLine[]>([]);
  const loadingMoreRef = useRef(false);

  // Keep linesRef in sync with lines state
  useEffect(() => {
    linesRef.current = lines;
  }, [lines]);

  useEffect(() => {
    if (type === "server" && !serverId) return;

    const wsv2 = getWsV2();
    maxIdRef.current = 0;
    loadingMoreRef.current = false;

    const onConnect = () => {
      setConnected(true);
      setLines([]);
      setHasMore(false);
      setIsLoadingMore(false);
      maxIdRef.current = 0;
      loadingMoreRef.current = false;
      wsv2.send({ msg: "load_log", type, ...(serverId ? { suid: serverId } : {}), limit: LOG_PAGE_SIZE });
    };

    const handleLoad = (msg: Record<string, unknown>) => {
      if (msg.type !== type) return;
      if (type === "server" && msg.suid !== serverId) return;
      const loaded = (msg.lines as LogLine[]) ?? [];
      const more = (msg.has_more as boolean) ?? false;
      if (loadingMoreRef.current) {
        loadingMoreRef.current = false;
        setIsLoadingMore(false);
        startTransition(() => setLines((prev) => [...loaded, ...prev]));
      } else {
        maxIdRef.current = loaded.reduce((m, l) => Math.max(m, l.line_number), 0);
        startTransition(() => setLines(loaded));
      }
      setHasMore(more);
    };

    const handleEvent = (data: Record<string, unknown>) => {
      const line = data as unknown as LogLine;
      if (line.line_number <= maxIdRef.current) return;
      maxIdRef.current = line.line_number;
      startTransition(() => setLines((prev) => [...prev, line]));
    };

    const unsubLoad = wsv2.subscribe("load_log_response", handleLoad, onConnect);
    const unsubEvent = wsv2.subscribeToEvent("log", type === "server" ? serverId! : null, handleEvent, type);

    return () => {
      setConnected(false);
      setLines([]);
      setHasMore(false);
      setIsLoadingMore(false);
      loadingMoreRef.current = false;
      unsubLoad();
      unsubEvent();
    };
  }, [type, serverId]);

  const loadMore = useCallback(() => {
    if (loadingMoreRef.current || !hasMore) return;
    const oldestId = linesRef.current[0]?.id;
    if (oldestId == null) return;
    loadingMoreRef.current = true;
    setIsLoadingMore(true);
    getWsV2().send({
      msg: "load_log",
      type,
      ...(serverId ? { suid: serverId } : {}),
      before_id: oldestId,
      limit: LOG_PAGE_SIZE,
    });
  }, [hasMore, type, serverId]);

  const clear = useCallback(() => setLines([]), []);

  return { lines, connected, clear, isPending, hasMore, isLoadingMore, loadMore };
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
