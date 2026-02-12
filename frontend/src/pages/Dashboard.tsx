import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServerConfig, ServerStatus, HealthStatus } from "../api/types";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import HealthCard from "../components/HealthCard";
import { useServerStatus, useProxyStatus, useHealth, useSlots } from "../api/hooks";

interface ServerSnapshot {
  name: string;
  status: ServerStatus;
  health: HealthStatus | null;
}

function ModelPanel({ modelIndex, name, onSnapshot }: {
  modelIndex: number;
  name: string;
  onSnapshot: (index: number, snap: ServerSnapshot) => void;
}) {
  const navigate = useNavigate();
  const { status, refresh } = useServerStatus(modelIndex);
  const slots = useSlots(modelIndex);
  const health = useHealth(modelIndex);

  useEffect(() => {
    onSnapshot(modelIndex, { name, status, health });
  }, [modelIndex, name, status, health, onSnapshot]);

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        status={status}
        slots={slots}
        modelIndex={modelIndex}
        onClick={() => navigate(`/logs/${modelIndex}`)}
      />
      <ServerControls status={status} modelIndex={modelIndex} onAction={refresh} />
    </div>
  );
}

export default function Dashboard() {
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus();
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [snapshots, setSnapshots] = useState<Map<number, ServerSnapshot>>(new Map());

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const handleSnapshot = useCallback((index: number, snap: ServerSnapshot) => {
    setSnapshots((prev) => {
      const next = new Map(prev);
      next.set(index, snap);
      return next;
    });
  }, []);

  const models = config?.models ?? [];
  const servers = models.map((_, i) => snapshots.get(i)).filter((s): s is ServerSnapshot => s != null);

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
            onSnapshot={handleSnapshot}
          />
        ))}
      </div>
      <HealthCard proxyStatus={proxyStatus} servers={servers} />
    </div>
  );
}
