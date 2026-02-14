import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServerConfig, ServerStatus, HealthStatus } from "../api/types";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import HealthCard from "../components/HealthCard";
import {
  useServerStatus,
  useProxyStatus,
  useHealth,
  useSlots,
  pollRatesFromConfig,
} from "../api/hooks";

interface ServerSnapshot {
  name: string;
  status: ServerStatus;
  health: HealthStatus | null;
  autoStart: boolean;
  hasTTL: boolean;
}

interface PollRates {
  serverStatus?: number;
  proxyStatus?: number;
  health?: number;
  slots?: number;
  slotsActive?: number;
}

function ModelPanel({
  modelIndex,
  name,
  isRemote,
  remoteAddress,
  autoStart,
  hasTTL,
  onSnapshot,
  poll,
}: {
  modelIndex: number;
  name: string;
  isRemote: boolean;
  remoteAddress?: string;
  autoStart: boolean;
  hasTTL: boolean;
  onSnapshot: (index: number, snap: ServerSnapshot) => void;
  poll?: PollRates;
}) {
  const navigate = useNavigate();
  const { status, refresh } = useServerStatus(modelIndex, poll?.serverStatus);
  const slots = useSlots(
    modelIndex,
    poll?.slots,
    poll?.slotsActive,
    status.state,
  );
  const health = useHealth(modelIndex, poll?.health);

  useEffect(() => {
    onSnapshot(modelIndex, { name, status, health, autoStart, hasTTL });
  }, [modelIndex, name, status, health, autoStart, hasTTL, onSnapshot]);

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        status={status}
        slots={slots}
        modelIndex={modelIndex}
        onClick={isRemote ? undefined : () => navigate(`/logs/${modelIndex}`)}
        remoteAddress={remoteAddress}
        health={isRemote ? health : undefined}
      />
      {!isRemote && (
        <ServerControls
          status={status}
          modelIndex={modelIndex}
          onAction={refresh}
        />
      )}
    </div>
  );
}

export default function Dashboard() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const poll = pollRatesFromConfig(config);
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus(
    poll.proxyStatus,
  );
  const [snapshots, setSnapshots] = useState<Map<number, ServerSnapshot>>(
    new Map(),
  );

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
  const servers = models
    .map((_, i) => snapshots.get(i))
    .filter((s): s is ServerSnapshot => s != null);

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
            name={m.name ?? `Llama Server ${i + 1}`}
            isRemote={(m.type ?? "local") === "remote"}
            remoteAddress={m["remote-address"]}
            autoStart={m["auto-start"] ?? false}
            hasTTL={m["model-ttl"] != null}
            onSnapshot={handleSnapshot}
            poll={poll}
          />
        ))}
      </div>
      <HealthCard proxyStatus={proxyStatus} servers={servers} />
    </div>
  );
}
