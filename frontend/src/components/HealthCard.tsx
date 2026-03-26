import { Fragment } from "react";
import type { HealthStatus, ProxyStatus, ServerStatus, UplinkStatus } from "../api/types";

interface ServerInfo {
  name: string;
  status: ServerStatus | null;
  health: HealthStatus | null;
  autoStart: boolean;
  hasTTL: boolean;
}

interface Props {
  proxyStatus: ProxyStatus;
  servers: ServerInfo[];
  uplink?: UplinkStatus;
}

const Check = () => <span className="text-green-500">&#10003;</span>;
const Cross = () => <span className="text-red-500">&#10005;</span>;
const Dot = () => <span className="text-gray-500">&#9679;</span>;
const Dash = () => <span className="text-yellow-500">&#9679;</span>;
const Question = () => <span className="text-yellow-500">?</span>;

/**
 * A stopped server that isn't expected to be running right now.
 * - Has TTL: may be idle-stopped, JIT will restart on demand.
 * - No auto-start and no TTL: manually managed.
 * - Auto-start without TTL: should always be running — NOT offline.
 */
const isOffline = (s: ServerInfo) =>
  s.status?.state === "stopped" && (s.hasTTL || !s.autoStart);

export default function HealthCard({ proxyStatus, servers, uplink }: Props) {
  const proxyOk = proxyStatus.state === "running";
  const serverUnknown = (s: ServerInfo) => s.status === null || s.status.state === "unknown" || s.health === null;
  const serverOk = (s: ServerInfo) => {
    const debug = s.name === "Qwen 3.5";
    if (s.status === null) {
      if (debug) {
        console.log("MiniMax M2.5 - Server", s.name, "status is null");
      }
      return false;
    }
    if (isOffline(s)) {
      if (debug) {
        console.log("MiniMax M2.5 - Server", s.name, "is offline");
      }
      return true;
    }
    if (debug) {
      console.log("MiniMax M2.5 - Health OK Check", s.health?.status);
    }
    const healthOk =
      s.health?.status === "ok" || s.health?.status === "no slot available";
    if (s.status.state === "remote") {
      if (debug) {
        console.log("MiniMax M2.5 - Server", s.name, "is remote", s.health?.status);
      }
      return healthOk || s.health?.status === "unknown";
    }
    return s.status.state === "running" && healthOk;
  };
  const allServersOk = servers.every(serverOk);
  const anyUnknown = servers.some(serverUnknown);
  const allOk = proxyOk && allServersOk && !anyUnknown;

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Health
      </h3>
      <div
        className="inline-grid items-center gap-x-3 gap-y-2"
        style={{ gridTemplateColumns: "auto auto auto" }}
      >
        {proxyOk ? <Check /> : <Cross />}
        <span className="text-sm text-gray-300">Proxy Server</span>
        <span
          className={`text-sm ${proxyOk ? "text-green-400" : "text-red-400"}`}
        >
          {proxyOk ? "running" : proxyStatus.state}
        </span>
        {uplink?.enabled && (
          <Fragment>
            {uplink.connected_clients > 0 ? <Check /> : <Dash />}
            <span className="text-sm text-gray-300">Uplink</span>
            <span className={`text-sm ${uplink.connected_clients > 0 ? "text-green-400" : "text-yellow-400"}`}>
              {uplink.connected_clients > 0 ? `connected (${uplink.connected_clients})` : "enabled"}
            </span>
          </Fragment>
        )}
        {servers.map((s) => {
          const unknown = serverUnknown(s);
          const ok = serverOk(s);
          const offline = isOffline(s);
          const isRemote = s.status?.state === "remote";
          if (s.name === "MiniMax M2.5") {
            console.log("Server", s.name, "unknown", unknown, "ok", ok, "offline", offline, "isRemote", isRemote, s.status);
          }
          const displayState = unknown
            ? "unknown"
            : offline
              ? "offline"
              : isRemote
                ? s.health?.status === "ok"
                  ? "running"
                  : (s.health?.status ?? "offline")
                : s.status!.state;
          const stateColor = unknown
            ? "text-yellow-400"
            : offline
              ? "text-gray-500"
              : ok
                ? "text-green-400"
                : displayState === "starting" || displayState === "unknown"
                  ? "text-yellow-400"
                  : "text-red-400";
          return (
            <Fragment key={s.name}>
              {unknown ? <Question /> : offline ? <Dot /> : ok ? <Check /> : <Cross />}
              <span className="text-sm text-gray-300">
                {s.name}
                {isRemote && (
                  <span className="ml-2 text-xs text-gray-600">remote</span>
                )}
              </span>
              <span className={`text-sm ${stateColor}`}>
                {displayState}
                {s.health &&
                  (s.health.slots_idle != null ||
                    s.health.slots_processing != null) && (
                    <span className="ml-2 text-gray-500">
                      Idle: {s.health.slots_idle ?? "?"} &middot; Processing:{" "}
                      {s.health.slots_processing ?? "?"}
                    </span>
                  )}
              </span>
            </Fragment>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-2">
        {allOk ? <Check /> : anyUnknown ? <Question /> : <Cross />}
        <span className="text-lg font-medium">
          {allOk ? "Healthy" : anyUnknown ? "Degraded (unknown)" : "Degraded"}
        </span>
      </div>
    </div>
  );
}
