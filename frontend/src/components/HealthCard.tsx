import { Fragment } from "react";
import type { HealthStatus, ProxyStatus, ServerStatus } from "../api/types";

interface ServerInfo {
  name: string;
  status: ServerStatus;
  health: HealthStatus | null;
  autoStart: boolean;
  hasTTL: boolean;
}

interface Props {
  proxyStatus: ProxyStatus;
  servers: ServerInfo[];
}

const Check = () => <span className="text-green-500">&#10003;</span>;
const Cross = () => <span className="text-red-500">&#10005;</span>;
const Dot = () => <span className="text-gray-500">&#9679;</span>;

/**
 * A stopped server that isn't expected to be running right now.
 * - Has TTL: may be idle-stopped, JIT will restart on demand.
 * - No auto-start and no TTL: manually managed.
 * - Auto-start without TTL: should always be running — NOT offline.
 */
const isOffline = (s: ServerInfo) =>
  s.status.state === "stopped" && (s.hasTTL || !s.autoStart);

export default function HealthCard({ proxyStatus, servers }: Props) {
  const proxyOk = proxyStatus.state === "running";
  const serverOk = (s: ServerInfo) => {
    if (isOffline(s)) return true;
    const healthOk =
      s.health?.status === "ok" || s.health?.status === "no slot available";
    if (s.status.state === "remote")
      return healthOk || s.health?.status === "unknown";
    return s.status.state === "running" && healthOk;
  };
  const allServersOk = servers.every(serverOk);
  const allOk = proxyOk && allServersOk;

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
        {servers.map((s) => {
          const ok = serverOk(s);
          const offline = isOffline(s);
          const isRemote = s.status.state === "remote";
          const displayState = offline
            ? "offline"
            : isRemote
              ? s.health?.status === "ok"
                ? "running"
                : (s.health?.status ?? "offline")
              : s.status.state;
          const stateColor = offline
            ? "text-gray-500"
            : ok
              ? "text-green-400"
              : displayState === "starting" || displayState === "unknown"
                ? "text-yellow-400"
                : "text-red-400";
          return (
            <Fragment key={s.name}>
              {offline ? <Dot /> : ok ? <Check /> : <Cross />}
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
        {allOk ? <Check /> : <Cross />}
        <span className="text-lg font-medium">
          {allOk ? "Healthy" : "Degraded"}
        </span>
      </div>
    </div>
  );
}
