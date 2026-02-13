import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServerConfig } from "../api/types";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import LogViewer from "../components/LogViewer";
import { useServerStatus, useProxyStatus, useLogs, useSlots } from "../api/hooks";

function ModelLogCard({ modelIndex, name, source, navigate }: { modelIndex: number; name: string; source: string; navigate: (path: string) => void }) {
  const { status, refresh } = useServerStatus(modelIndex);
  const slots = useSlots(modelIndex);

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        status={status}
        slots={slots}
        modelIndex={modelIndex}
        onClick={() => navigate(`/logs/${modelIndex}`)}
        selected={source === String(modelIndex)}
      />
      <ServerControls status={status} modelIndex={modelIndex} onAction={refresh} />
    </div>
  );
}

export default function Logs() {
  const { source = "proxy" } = useParams<{ source: string }>();
  const navigate = useNavigate();
  const wsSource = source === "proxy" ? "proxy" : `model-${source}`;
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus();
  const { lines, connected, clear } = useLogs(wsSource);
  const [config, setConfig] = useState<ServerConfig | null>(null);

  useEffect(() => { document.title = "Llama Manager - Logs"; }, []);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const models = config?.models ?? [];
  const modelIndex = source === "proxy" ? null : Number(source);
  const logHeader = source === "proxy"
    ? "Proxy Server"
    : models[modelIndex!]?.name ?? `Llama Server ${(modelIndex ?? 0) + 1}`;

  // Redirect to proxy logs if current source is a remote model
  useEffect(() => {
    if (modelIndex != null && models.length > 0 && modelIndex < models.length) {
      if ((models[modelIndex].type ?? "local") === "remote") {
        navigate("/logs/proxy", { replace: true });
      }
    }
  }, [modelIndex, models, navigate]);

  // Only show local models in the card list
  const localModels = models
    .map((m, i) => ({ model: m, index: i }))
    .filter(({ model }) => (model.type ?? "local") !== "remote");

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-4">Logs</h1>
      <div className="flex gap-6 items-start mb-4 flex-wrap">
        <div className="space-y-4">
          <ProxyStatusCard
            status={proxyStatus}
            onClick={() => navigate("/logs/proxy")}
            selected={source === "proxy"}
          />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        {localModels.map(({ model: m, index: i }) => (
          <ModelLogCard
            key={i}
            modelIndex={i}
            name={m.name ?? `Llama Server ${i + 1}`}
            source={source}
            navigate={navigate}
          />
        ))}
      </div>
      <h2 className="text-lg font-semibold mb-2">{logHeader} Logs</h2>
      <div className="flex-1 min-h-0">
        <LogViewer lines={lines} connected={connected} onClear={clear} source={wsSource} />
      </div>
    </div>
  );
}
