import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServerConfig, ServerStatus, HealthStatus, RemoteManagerStatus } from "../api/types";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import HealthCard from "../components/HealthCard";
import {
  useProxyStatusWS,
  useServerStatusWS,
  useRemotes,
  useUplinkStatus,
  useHealthStream,
} from "../api/hooks";

interface ServerSnapshot {
  name: string;
  status: ServerStatus | null;
  health: HealthStatus | null;
  autoStart: boolean;
  hasTTL: boolean;
}

function ModelPanel({
  modelIndex,
  modelSuid,
  name,
  isRemote,
  remoteAddress,
  autoStart,
  hasTTL,
  onSnapshot,
}: {
  modelIndex: number;
  modelSuid: string;
  name: string;
  isRemote: boolean;
  remoteAddress?: string;
  autoStart: boolean;
  hasTTL: boolean;
  onSnapshot: (index: number, snap: ServerSnapshot) => void;
}) {
  const navigate = useNavigate();
  const { status, refresh } = useServerStatusWS(modelSuid);
  const health = useHealthStream(modelSuid);
  const statusOrUnknown = status ?? { state: "unknown" as const, pid: null, host: null, port: null, uptime: null };

  useEffect(() => {
    onSnapshot(modelIndex, { name, status, health, autoStart, hasTTL });
  }, [modelIndex, name, status, health, autoStart, hasTTL, onSnapshot]);

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelSuid={modelSuid}
        status={statusOrUnknown}
        onClick={isRemote ? undefined : () => navigate(`/logs/${modelSuid}`)}
        remoteAddress={remoteAddress}
        health={isRemote ? health : undefined}
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
  name,
}: {
  modelSuid: string;
  name: string;
}) {
  const navigate = useNavigate();
  const { status, refresh } = useServerStatusWS(modelSuid);
  const statusOrUnknown = status ?? { state: "unknown" as const, pid: null, host: null, port: null, uptime: null };

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelSuid={modelSuid}
        status={statusOrUnknown}
        onClick={() => navigate(`/logs/${modelSuid}`)}
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

function RemoteManagerSection({ rm }: { rm: RemoteManagerStatus }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 mt-2">
        <span className={`h-2 w-2 rounded-full flex-shrink-0 ${connectionDot(rm.connection_state)}`} />
        <span className="text-sm font-medium text-gray-300">
          {rm.name ?? rm.url}
        </span>
        {rm.name && (
          <span className="text-xs text-gray-600">{rm.url}</span>
        )}
        {rm.connection_state !== "connected" && (
          <span className="text-xs text-gray-600 capitalize">{rm.connection_state}</span>
        )}
      </div>
      {rm.models.length === 0 && rm.connection_state !== "connected" ? null : (
        <div className="flex gap-6 flex-wrap ml-3">
          {rm.models.map((m) => (
            <RemoteModelPanel
              key={m.suid}
              modelSuid={m.suid}
              name={m.name ?? "Remote Model"}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatusWS();
  const [snapshots, setSnapshots] = useState<Map<number, ServerSnapshot>>(
    new Map(),
  );
  const remotes = useRemotes();
  const uplink = useUplinkStatus();

  useEffect(() => {
    document.title = "Llama Manager - Dashboard";
  }, []);

  useEffect(() => {
    api
      .getConfig()
      .then(setConfig)
      .catch(() => {});
  }, []);

  const handleSnapshot = useCallback((index: number, snap: ServerSnapshot) => {
    setSnapshots((prev) => {
      const next = new Map(prev);
      next.set(index, snap);
      return next;
    });
  }, []);

  const models = config?.models ?? [];
  const servers = Array.from(snapshots.values());

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="flex gap-6 items-start flex-wrap">
        <div className="space-y-4">
          <ProxyStatusCard status={proxyStatus} onClick={() => {}} />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        {models.map((m, i) => (
          <ModelPanel
            key={i}
            modelIndex={i}
            modelSuid={m.suid}
            name={m.name ?? `Llama Server ${i + 1}`}
            isRemote={(m.type ?? "local") === "remote"}
            remoteAddress={m.remote_address}
            autoStart={m.auto_start ?? false}
            hasTTL={m.model_ttl != null}
            onSnapshot={handleSnapshot}
          />
        ))}
      </div>
      {remotes.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold text-gray-400">Remotes</h2>
          {remotes.map((rm) => (
            <RemoteManagerSection key={rm.index} rm={rm} />
          ))}
        </div>
      )}
      <HealthCard proxyStatus={proxyStatus} servers={servers} remotes={remotes} uplink={uplink ?? undefined} />
    </div>
  );
}
