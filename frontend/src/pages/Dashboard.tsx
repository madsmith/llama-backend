import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServerConfig, ServerStatus, HealthStatus, RemoteManagerStatus, ModelConfig } from "../api/types";

function effectiveModelId(m: ModelConfig): string {
  if (m.id) return m.id;
  if (m.type === "remote") {
    if (m.remote_model_id) return m.remote_model_id;
    if (m.remote_address) {
      const stripped = m.remote_address.replace(/\/+$/, "");
      const lastSegment = stripped.split("/").pop() ?? "";
      return lastSegment.split(":")[0];
    }
    return "";
  }
  if (m.model_path) {
    const basename = m.model_path.split("/").pop() ?? "";
    const dotIdx = basename.lastIndexOf(".");
    const stem = dotIdx > 0 ? basename.slice(0, dotIdx) : basename;
    return stem.toLowerCase();
  }
  return "";
}
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import HealthCard from "../components/HealthCard";
import ToggleSelector from "../components/inputs/ToggleSelector";
import {
  useProxyStatusWS,
  useServerStatusWS,
  useRemotes,
  useAutoloaded,
  useUplinkStatus,
  useHealthStream,
} from "../api/hooks";
import { usePref } from "../api/prefs";

interface ServerSnapshot {
  name: string;
  status: ServerStatus | null;
  health: HealthStatus | null;
  autoStart: boolean;
  hasTTL: boolean;
}

function ModelPanel({
  modelSuid,
  name,
  modelId,
  isRemote,
  remoteAddress,
  autoStart,
  hasTTL,
  allowProxy,
  proxyBaseUrl,
  hideStopped,
  onSnapshot,
}: {
  modelSuid: string;
  name: string;
  modelId?: string | null;
  isRemote: boolean;
  remoteAddress?: string;
  autoStart: boolean;
  hasTTL: boolean;
  allowProxy: boolean;
  proxyBaseUrl: string;
  hideStopped: boolean;
  onSnapshot: (suid: string, snap: ServerSnapshot) => void;
}) {
  const navigate = useNavigate();
  const { status, refresh } = useServerStatusWS(modelSuid);
  const health = useHealthStream(modelSuid);
  const statusOrUnknown = status ?? { state: "unknown" as const, pid: null, host: null, port: null, uptime: null };

  useEffect(() => {
    onSnapshot(modelSuid, { name, status, health, autoStart, hasTTL });
  }, [modelSuid, name, status, health, autoStart, hasTTL, onSnapshot]);

  // Only hide servers that are stopped by design (TTL-managed or not auto-started);
  // a stopped auto-start server is faulted and must stay visible.
  if (hideStopped && status?.state === "stopped" && (hasTTL || !autoStart)) return null;

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelId={modelId}
        modelSuid={modelSuid}
        status={statusOrUnknown}
        onClick={isRemote ? undefined : () => navigate(`/logs/${modelSuid}`)}
        remoteAddress={remoteAddress}
        health={isRemote ? health : undefined}
        allowProxy={allowProxy}
        proxyBaseUrl={proxyBaseUrl}
      />
      {!isRemote && (
        <ServerControls
          status={statusOrUnknown}
          modelSuid={modelSuid}
          onAction={refresh}
        />
      )}
    </div>
  );
}

function RemoteModelPanel({
  modelSuid,
  modelId,
  name,
  autoStart,
  hasTTL,
  allowProxy,
  proxyBaseUrl,
  hideStopped,
  autoloaded = false,
  listedState,
}: {
  modelSuid: string;
  modelId: string;
  name: string;
  autoStart: boolean;
  hasTTL: boolean;
  allowProxy: boolean;
  proxyBaseUrl: string;
  hideStopped: boolean;
  autoloaded?: boolean;
  listedState: string;
}) {
  const navigate = useNavigate();
  const { status, refresh } = useServerStatusWS(modelSuid);
  const statusOrUnknown = status ?? { state: "unknown" as const, pid: null, host: null, port: null, uptime: null };
  const state = status?.state ?? listedState;

  // Autoloaded servers are shown only while loading or loaded, like local ones.
  if (autoloaded && (state === "stopped" || state === "unknown")) return null;
  if (hideStopped && status?.state === "stopped" && (hasTTL || !autoStart)) return null;

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelId={modelId}
        modelSuid={modelSuid}
        status={statusOrUnknown}
        onClick={() => navigate(`/logs/${modelSuid}`)}
        allowProxy={allowProxy}
        proxyBaseUrl={proxyBaseUrl}
      />
      <ServerControls status={status} modelSuid={modelSuid} onAction={refresh} />
    </div>
  );
}

function connectionDot(state: RemoteManagerStatus["connection_state"]) {
  if (state === "connected") return "bg-green-500";
  if (state === "connecting") return "bg-yellow-500 animate-pulse";
  return "bg-gray-600";
}

function remoteHref(url: string) {
  return /^https?:\/\//i.test(url) ? url : `http://${url}`;
}

function RemoteManagerSection({
  rm,
  proxyBaseUrl,
  hideStopped,
}: {
  rm: RemoteManagerStatus;
  proxyBaseUrl: string;
  hideStopped: boolean;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 mt-2">
        <span className={`h-2 w-2 rounded-full flex-shrink-0 ${connectionDot(rm.connection_state)}`} />
        <span className="text-sm font-medium text-gray-300">
          {rm.name ?? (
            <a href={remoteHref(rm.url)} target="_blank" rel="noreferrer" className="hover:text-gray-100">
              {rm.url}
            </a>
          )}
        </span>
        {rm.name && (
          <a href={remoteHref(rm.url)} target="_blank" rel="noreferrer" className="text-xs text-gray-600 hover:text-gray-400">
            {rm.url}
          </a>
        )}
        {rm.connection_state !== "connected" && (
          <span className="text-xs text-gray-600 capitalize">{rm.connection_state}</span>
        )}
      </div>
      {rm.models.length === 0 && rm.connection_state !== "connected" ? null : (
        <div className="flex gap-6 flex-wrap ml-3">
          {/* Configured models first, then autoloaded ones. */}
          {[...rm.models.filter((m) => !m.autoloaded), ...rm.models.filter((m) => m.autoloaded)].map((m) => (
            <RemoteModelPanel
              key={m.suid}
              modelSuid={m.suid}
              modelId={m.model_id}
              name={m.autoloaded && m.name ? `${m.name.split("/").pop()} (autoloaded)` : m.name ?? "Remote Model"}
              autoStart={m.auto_start}
              hasTTL={m.has_ttl}
              allowProxy={m.allow_proxy ?? true}
              proxyBaseUrl={proxyBaseUrl}
              hideStopped={hideStopped}
              autoloaded={m.autoloaded}
              listedState={m.state}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatusWS();
  const [snapshots, setSnapshots] = useState<Map<string, ServerSnapshot>>(
    new Map(),
  );
  const remotes = useRemotes();
  const autoloaded = useAutoloaded();
  const uplink = useUplinkStatus();
  const [controlsOpen, setControlsOpen] = useState(false);
  const [hideStopped, setHideStopped] = usePref("pref:hideStopped", false);

  useEffect(() => {
    document.title = "Llama Manager - Dashboard";
  }, []);

  useEffect(() => {
    api
      .getConfig()
      .then(setConfig)
      .catch(() => {});
  }, []);

  const handleSnapshot = useCallback((suid: string, snap: ServerSnapshot) => {
    setSnapshots((prev) => {
      const next = new Map(prev);
      next.set(suid, snap);
      return next;
    });
  }, []);

  const models = config?.models ?? [];
  // Autoloaded servers appear while loaded (or failed) and vanish once unloaded.
  const loadedAuto = autoloaded.filter((a) => a.state !== "stopped" && a.state !== "unknown");
  const shown = new Set([...models.map((m) => m.suid), ...loadedAuto.map((a) => a.suid)]);
  const servers = Array.from(snapshots.entries())
    .filter(([suid]) => shown.has(suid))
    .map(([, snap]) => snap);
  const proxyBaseUrl = `http://${window.location.hostname}:${config?.api_server.port ?? 1234}`;

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <button
            onClick={() => setControlsOpen((o) => !o)}
            aria-expanded={controlsOpen}
            aria-label={controlsOpen ? "Hide dashboard controls" : "Show dashboard controls"}
            className="p-1 rounded text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 14 14"
              className={`w-4 h-4 transition-transform duration-200 ${controlsOpen ? "rotate-45" : ""}`}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="7" y1="2" x2="7" y2="12" />
              <line x1="2" y1="7" x2="12" y2="7" />
            </svg>
          </button>
        </div>
        <div
          className={`grid transition-all duration-200 ease-out ${
            controlsOpen ? "grid-rows-[1fr] opacity-100 mt-3" : "grid-rows-[0fr] opacity-0"
          }`}
          inert={!controlsOpen}
        >
          <div className="overflow-hidden">
            <div className="flex items-center gap-6 px-4 py-3 rounded-lg border border-gray-800 bg-gray-900">
              <ToggleSelector label="Hide stopped" checked={hideStopped} onChange={setHideStopped} />
            </div>
          </div>
        </div>
      </div>
      <div className="flex gap-6 items-start flex-wrap">
        <div className="space-y-4">
          <ProxyStatusCard status={proxyStatus} onClick={() => navigate("/logs/proxy")} />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        {models.map((m, i) => (
          <ModelPanel
            key={m.suid}
            modelSuid={m.suid}
            name={m.name ?? `Llama Server ${i + 1}`}
            modelId={effectiveModelId(m)}
            isRemote={(m.type ?? "local") === "remote"}
            remoteAddress={m.remote_address}
            autoStart={m.auto_start ?? false}
            hasTTL={m.model_ttl != null}
            allowProxy={m.allow_proxy ?? true}
            proxyBaseUrl={proxyBaseUrl}
            hideStopped={hideStopped}
            onSnapshot={handleSnapshot}
          />
        ))}
      </div>
      {loadedAuto.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-gray-400">Autoloaded</h2>
          <div className="flex gap-6 items-start flex-wrap">
            {loadedAuto.map((a) => (
              <ModelPanel
                key={a.suid}
                modelSuid={a.suid}
                name={a.name.split("/").pop() ?? a.name}
                modelId={a.model_id}
                isRemote={false}
                autoStart={false}
                hasTTL
                allowProxy
                proxyBaseUrl={proxyBaseUrl}
                hideStopped={hideStopped}
                onSnapshot={handleSnapshot}
              />
            ))}
          </div>
        </div>
      )}
      {remotes.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-gray-400">Remotes</h2>
          {remotes.map((rm) => (
            <RemoteManagerSection key={rm.index} rm={rm} proxyBaseUrl={proxyBaseUrl} hideStopped={hideStopped} />
          ))}
        </div>
      )}
      <HealthCard proxyStatus={proxyStatus} servers={servers} remotes={remotes} uplink={uplink ?? undefined} />
    </div>
  );
}
