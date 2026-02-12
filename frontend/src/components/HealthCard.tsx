import type { HealthStatus, ProxyStatus, ServerStatus } from "../api/types";

interface Props {
  proxyStatus: ProxyStatus;
  serverStatus: ServerStatus;
  serverName: string;
  health: HealthStatus | null;
}

export default function HealthCard({ proxyStatus, serverStatus, serverName, health }: Props) {
  const proxyOk = proxyStatus.state === "running";
  const serverOk = serverStatus.state === "running";
  const llamaOk = health?.status === "ok" || health?.status === "no slot available";
  const allOk = proxyOk && serverOk && llamaOk;

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Health
      </h3>
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${proxyOk ? "bg-green-500" : "bg-red-500"}`} />
          <span className="text-sm text-gray-300">Proxy Server</span>
          <span className={`text-sm ${proxyOk ? "text-green-400" : "text-red-400"}`}>
            {proxyOk ? "running" : proxyStatus.state}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${serverOk ? "bg-green-500" : serverStatus.state === "starting" ? "bg-yellow-500" : "bg-red-500"}`} />
          <span className="text-sm text-gray-300">{serverName}</span>
          <span className={`text-sm ${serverOk ? "text-green-400" : serverStatus.state === "starting" ? "text-yellow-400" : "text-red-400"}`}>
            {serverStatus.state}
          </span>
          {health && (health.slots_idle != null || health.slots_processing != null) && (
            <span className="text-sm text-gray-500">
              &middot; Idle: {health.slots_idle ?? "?"} &middot; Processing: {health.slots_processing ?? "?"}
            </span>
          )}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <span className={`h-4 w-4 rounded-full ${allOk ? "bg-green-500" : "bg-red-500"}`} />
        <span className="text-lg font-medium">{allOk ? "Healthy" : "Degraded"}</span>
      </div>
    </div>
  );
}
