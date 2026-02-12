import { useEffect, useState } from "react";
import { api } from "../api/client";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import HealthCard from "../components/HealthCard";
import { useServerStatus, useProxyStatus, useHealth, useSlots } from "../api/hooks";

export default function Dashboard() {
  const { status, refresh } = useServerStatus();
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus();
  const health = useHealth();
  const slots = useSlots();
  const [modelName, setModelName] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then((cfg) => setModelName(cfg.models[0]?.name ?? null)).catch(() => {});
  }, []);

  const serverLabel = modelName ?? "Llama Server 1";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="flex gap-6 items-start">
        <div className="space-y-4">
          <ProxyStatusCard status={proxyStatus} />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        <div className="space-y-4">
          <ServerStatusCard name={serverLabel} status={status} slots={slots} modelIndex={0} />
          <ServerControls status={status} onAction={refresh} />
        </div>
      </div>
      <HealthCard proxyStatus={proxyStatus} serverStatus={status} serverName={serverLabel} health={health} />
    </div>
  );
}
