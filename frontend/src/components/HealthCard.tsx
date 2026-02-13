import type { HealthStatus, ProxyStatus, ServerStatus } from "../api/types";

interface ServerInfo {
  name: string;
  status: ServerStatus;
  health: HealthStatus | null;
}

interface Props {
  proxyStatus: ProxyStatus;
  servers: ServerInfo[];
}

const Check = () => <span className="text-green-500">&#10003;</span>;
const Cross = () => <span className="text-red-500">&#10005;</span>;

export default function HealthCard({ proxyStatus, servers }: Props) {
  const proxyOk = proxyStatus.state === "running";
  const serverOk = (s: ServerInfo) => {
    const healthOk = s.health?.status === "ok" || s.health?.status === "no slot available";
    if (s.status.state === "remote") return healthOk || s.health?.status === "unknown";
    return s.status.state === "running" && healthOk;
  };
  const allServersOk = servers.every(serverOk);
  const allOk = proxyOk && allServersOk;

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Health
      </h3>
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          {proxyOk ? <Check /> : <Cross />}
          <span className="text-sm text-gray-300">Proxy Server</span>
          <span className={`text-sm ${proxyOk ? "text-green-400" : "text-red-400"}`}>
            {proxyOk ? "running" : proxyStatus.state}
          </span>
        </div>
        {servers.map((s) => {
          const ok = serverOk(s);
          const isRemote = s.status.state === "remote";
          const displayState = isRemote
            ? (s.health?.status === "ok" ? "running" : s.health?.status ?? "offline")
            : s.status.state;
          return (
            <div key={s.name} className="flex items-center gap-3">
              {ok ? <Check /> : <Cross />}
              <span className="text-sm text-gray-300">{s.name}</span>
              <span className={`text-sm ${ok ? "text-green-400" : displayState === "starting" || displayState === "unknown" ? "text-yellow-400" : "text-red-400"}`}>
                {displayState}
              </span>
              {isRemote && <span className="text-xs text-gray-600">remote</span>}
              {s.health && (s.health.slots_idle != null || s.health.slots_processing != null) && (
                <span className="text-sm text-gray-500">
                  &middot; Idle: {s.health.slots_idle ?? "?"} &middot; Processing: {s.health.slots_processing ?? "?"}
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-2">
        {allOk ? <Check /> : <Cross />}
        <span className="text-lg font-medium">{allOk ? "Healthy" : "Degraded"}</span>
      </div>
    </div>
  );
}
