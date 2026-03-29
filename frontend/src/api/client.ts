import type {
  ServerStatus,
  ServerConfig,
  ProxyStatus,
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
  start: (serverId: string, modelSuid: string) =>
    request<ServerStatus>(
      `/api/server/start?server_id=${encodeURIComponent(serverId)}&model_suid=${encodeURIComponent(modelSuid)}`,
      { method: "POST" },
    ),
  stop: (serverId: string, modelSuid: string) =>
    request<ServerStatus>(
      `/api/server/stop?server_id=${encodeURIComponent(serverId)}&model_suid=${encodeURIComponent(modelSuid)}`,
      { method: "POST" },
    ),
  restart: (serverId: string, modelSuid: string) =>
    request<ServerStatus>(
      `/api/server/restart?server_id=${encodeURIComponent(serverId)}&model_suid=${encodeURIComponent(modelSuid)}`,
      { method: "POST" },
    ),
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
  getSlots: (modelSuid: string) =>
    request<SlotInfo[]>(`/api/status/slots?model_suid=${encodeURIComponent(modelSuid)}`),
  cancelSlot: (modelSuid: string, slot: number) =>
    request<{ status: string }>(
      `/api/status/slots/cancel?model_suid=${encodeURIComponent(modelSuid)}&slot=${slot}`,
      { method: "POST" },
    ),
  getProps: (modelSuid: string) =>
    request<ModelProps>(`/api/status/props?model_suid=${encodeURIComponent(modelSuid)}`),
  getRequestLog: (requestId: string) =>
    request<RequestLogEntry>(`/api/status/requests/${requestId}`),
  generateUplinkToken: () =>
    getWsV2()
      .sendRequest<{ token: string }>({ msg: "generate_token" }, "generate_token_response"),
};

