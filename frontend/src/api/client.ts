import type {
  ServerStatus,
  ServerConfig,
  ProxyStatus,
  HealthStatus,
  SlotInfo,
  ModelProps,
  RequestLogEntry,
} from "./types";
import { getWsV2 } from "./wsv2";

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
  getStatus: (model = 0) =>
    request<ServerStatus>(`/api/server/status?model=${model}`),
  start: (model = 0) =>
    request<ServerStatus>(`/api/server/start?model=${model}`, {
      method: "POST",
    }),
  stop: (model = 0) =>
    request<ServerStatus>(`/api/server/stop?model=${model}`, {
      method: "POST",
    }),
  restart: (model = 0) =>
    request<ServerStatus>(`/api/server/restart?model=${model}`, {
      method: "POST",
    }),
  getProxyStatus: () => request<ProxyStatus>("/api/server/proxy-status"),
  proxyStart: () =>
    request<ProxyStatus>("/api/server/proxy-start", { method: "POST" }),
  proxyStop: () =>
    request<ProxyStatus>("/api/server/proxy-stop", { method: "POST" }),
  proxyRestart: () =>
    request<ProxyStatus>("/api/server/proxy-restart", { method: "POST" }),
  getConfig: () =>
    getWsV2()
      .sendRequest<{ config: ServerConfig }>({ msg: "get_config" }, "get_config_response")
      .then((r) => r.config),
  putConfig: (cfg: ServerConfig) =>
    getWsV2()
      .sendRequest<{ config: ServerConfig }>({ msg: "put_config", config: cfg }, "put_config_response")
      .then((r) => r.config),
  getHealth: (model = 0) =>
    request<HealthStatus>(`/api/status/health?model=${model}`),
  getSlots: (model = 0) =>
    request<SlotInfo[]>(`/api/status/slots?model=${model}`),
  cancelSlot: (model: number, slot: number) =>
    request<{ status: string }>(
      `/api/status/slots/cancel?model=${model}&slot=${slot}`,
      { method: "POST" },
    ),
  getProps: (model = 0) =>
    request<ModelProps>(`/api/status/props?model=${model}`),
  getRequestLog: (requestId: string) =>
    request<RequestLogEntry>(`/api/status/requests/${requestId}`),
  generateUplinkToken: () =>
    getWsV2()
      .sendRequest<{ token: string }>({ msg: "generate_token" }, "generate_token_response"),
};

