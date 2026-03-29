import { Fragment } from "react";
import type { HealthStatus, ProxyStatus, RemoteManagerStatus, ServerStatus, UplinkStatus } from "../api/types";

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
  remotes?: RemoteManagerStatus[];
  uplink?: UplinkStatus;
}

const Check = () => <span className="text-green-500">&#10003;</span>;
const Cross = () => <span className="text-red-500">&#10005;</span>;
const Dot = () => <span className="text-gray-500">&#9679;</span>;
const Dash = () => <span className="text-yellow-500">&#9679;</span>;
const Question = () => <span className="text-yellow-500">?</span>;

/**
 * Four mutually exclusive health states for a server row:
 *  ok         — running and healthy
 *  offline_ok — stopped and expected to be down (TTL-managed or not auto-started)
 *  unknown    — no status data yet; status is still synchronizing
 *  degraded   — should be running but isn't, or running but unhealthy
 */
type DerivedState = "ok" | "offline_ok" | "unknown" | "degraded";

const isOfflineOk = (s: ServerInfo) =>
  s.status?.state === "stopped" && (s.hasTTL || !s.autoStart);

function classifyServer(s: ServerInfo): DerivedState {
  if (s.status == null) return "unknown";

  if (isOfflineOk(s)) return "offline_ok";

  if (s.status.state === "unknown") return "unknown";

  if (s.status.state === "remote") {
    if (s.health == null) return "unknown";
    if (
      s.health.status === "ok" ||
      s.health.status === "no slot available" ||
      s.health.status === "unknown" // tolerate stale remote health
    ) return "ok";
    return "degraded";
  }

  if (s.status.state === "running") {
    if (s.health == null) return "unknown";
    if (s.health.status === "ok" || s.health.status === "no slot available") return "ok";
    return "degraded";
  }

  // starting / stopping / error / crashed / etc.
  return "degraded";
}

export default function HealthCard({ proxyStatus, servers, remotes, uplink }: Props) {
  const proxyOk = proxyStatus.state === "running";

  const classifications = servers.map(classifyServer);
  const allServersOk = classifications.every(c => c === "ok" || c === "offline_ok");
  const anyServerUnknown = classifications.some(c => c === "unknown");

  const remoteModels = remotes?.flatMap(rm => rm.models) ?? [];
  const allRemotesOk = remoteModels.every(m =>
    m.state === "running" || m.state === "unknown" || (m.state === "stopped" && (m.has_ttl || !m.auto_start))
  );
  const anyRemoteUnknown = remoteModels.some(m => m.state === "unknown" || m.state === "starting");

  const anyUnknown = anyServerUnknown || anyRemoteUnknown;
  const allOk = proxyOk && allServersOk && !anyServerUnknown && allRemotesOk && !anyRemoteUnknown;

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
        <span className={`text-sm ${proxyOk ? "text-green-400" : "text-red-400"}`}>
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

        {servers.map((s, i) => {
          const classified = classifications[i];
          const state = s.status?.state;
          const isRemote = state === "remote";

          const displayState =
            classified === "offline_ok" ? "offline_ok"
            : isRemote
              ? (s.health?.status === "ok" ? "running" : (s.health?.status ?? "unknown"))
              : (state ?? "unknown");

          const stateColor =
            classified === "unknown" ? "text-yellow-400"
            : classified === "offline_ok" ? "text-gray-500"
            : classified === "ok" ? "text-green-400"
            : "text-red-400";

          return (
            <Fragment key={s.name}>
              {classified === "unknown" ? <Question />
                : classified === "offline_ok" ? <Dot />
                : classified === "ok" ? <Check />
                : <Cross />}
              <span className="text-sm text-gray-300">
                {s.name}
                {isRemote && <span className="ml-2 text-xs text-gray-600">remote</span>}
              </span>
              <span className={`text-sm ${stateColor}`}>
                {displayState}
                {s.health && (s.health.slots_idle != null || s.health.slots_processing != null) && (
                  <span className="ml-2 text-gray-500">
                    Idle: {s.health.slots_idle ?? "?"} &middot; Processing: {s.health.slots_processing ?? "?"}
                  </span>
                )}
              </span>
            </Fragment>
          );
        })}

        {remotes?.flatMap(rm =>
          rm.models.map(m => {
            const offlineOk = m.state === "stopped" && (m.has_ttl || !m.auto_start);
            const classified =
              m.state === "unknown" ? "unknown"
              : offlineOk ? "offline_ok"
              : m.state === "running" ? "ok"
              : m.state === "starting" ? "unknown"
              : "degraded";
            const stateColor =
              classified === "unknown" ? "text-yellow-400"
              : classified === "offline_ok" ? "text-gray-500"
              : classified === "ok" ? "text-green-400"
              : "text-red-400";
            return (
              <Fragment key={m.suid}>
                {classified === "unknown" ? <Question />
                  : classified === "offline_ok" ? <Dot />
                  : classified === "ok" ? <Check />
                  : <Cross />}
                <span className="text-sm text-gray-300">
                  {m.name ?? "Remote Model"}
                  <span className="ml-2 text-xs text-gray-600">{rm.name ?? rm.url}</span>
                </span>
                <span className={`text-sm ${stateColor}`}>{m.state}</span>
              </Fragment>
            );
          })
        )}
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
