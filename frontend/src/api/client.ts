import type { ServerStatus, ServerConfig, ProxyStatus, HealthStatus, SlotInfo, ModelProps } from "./types";

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const msg = body?.error ?? `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  getStatus: (model = 0) => request<ServerStatus>(`/api/server/status?model=${model}`),
  start: (model = 0) => request<ServerStatus>(`/api/server/start?model=${model}`, { method: "POST" }),
  stop: (model = 0) => request<ServerStatus>(`/api/server/stop?model=${model}`, { method: "POST" }),
  restart: (model = 0) => request<ServerStatus>(`/api/server/restart?model=${model}`, { method: "POST" }),
  getProxyStatus: () => request<ProxyStatus>("/api/server/proxy-status"),
  proxyStart: () => request<ProxyStatus>("/api/server/proxy-start", { method: "POST" }),
  proxyStop: () => request<ProxyStatus>("/api/server/proxy-stop", { method: "POST" }),
  proxyRestart: () => request<ProxyStatus>("/api/server/proxy-restart", { method: "POST" }),
  getConfig: () => request<ServerConfig>("/api/server/config"),
  putConfig: (cfg: ServerConfig) =>
    request<ServerConfig>("/api/server/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }),
  getHealth: (model = 0) => request<HealthStatus>(`/api/status/health?model=${model}`),
  getSlots: (model = 0) => request<SlotInfo[]>(`/api/status/slots?model=${model}`),
  getProps: (model = 0) => request<ModelProps>(`/api/status/props?model=${model}`),
};

export function wsUrl(source = "model-0"): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/logs?source=${source}`;
}
