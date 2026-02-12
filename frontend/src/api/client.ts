import type { ServerStatus, ServerConfig, HealthStatus, SlotInfo, ModelProps } from "./types";

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
  getStatus: () => request<ServerStatus>("/api/server/status"),
  start: () => request<ServerStatus>("/api/server/start", { method: "POST" }),
  stop: () => request<ServerStatus>("/api/server/stop", { method: "POST" }),
  restart: () => request<ServerStatus>("/api/server/restart", { method: "POST" }),
  getConfig: () => request<ServerConfig>("/api/server/config"),
  putConfig: (cfg: ServerConfig) =>
    request<ServerConfig>("/api/server/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }),
  getHealth: () => request<HealthStatus>("/api/status/health"),
  getSlots: () => request<SlotInfo[]>("/api/status/slots"),
  getProps: () => request<ModelProps>("/api/status/props"),
};

export function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws/logs`;
}
