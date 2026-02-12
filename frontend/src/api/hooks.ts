import { useState, useEffect, useRef, useCallback } from "react";
import { api, wsUrl } from "./client";
import type {
  ServerStatus,
  ProxyStatus,
  HealthStatus,
  SlotInfo,
  ModelProps,
  LogMessage,
} from "./types";

export function useServerStatus(modelIndex = 0, pollMs = 3000) {
  const [status, setStatus] = useState<ServerStatus>({
    state: "stopped",
    pid: null,
    uptime: null,
  });

  const poll = useCallback(() => {
    api.getStatus(modelIndex).then(setStatus).catch(() => {});
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
    api.getProxyStatus().then(setStatus).catch(() => {});
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

export function useSlots(modelIndex = 0, pollMs = 5000) {
  const [slots, setSlots] = useState<SlotInfo[]>([]);

  useEffect(() => {
    const fetch = () => {
      api
        .getSlots(modelIndex)
        .then(setSlots)
        .catch(() => setSlots([]));
    };
    fetch();
    const id = setInterval(fetch, pollMs);
    return () => clearInterval(id);
  }, [modelIndex, pollMs]);

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

export function useLogs(source = "model-0") {
  const [lines, setLines] = useState<{ id: number; text: string }[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const [serverState, setServerState] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLines([]);

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
          setLines((prev) => [...prev, { id: msg.id!, text: msg.text! }]);
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
    };
  }, [source]);

  const clear = useCallback(() => setLines([]), []);

  return { lines, connected, serverState, clear };
}
