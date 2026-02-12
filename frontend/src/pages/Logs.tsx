import { useEffect, useState } from "react";
import { api } from "../api/client";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import LogViewer from "../components/LogViewer";
import { useServerStatus, useProxyStatus, useLogs, useSlots } from "../api/hooks";

type LogSource = "proxy" | "model-0";

export default function Logs() {
  const { status, refresh } = useServerStatus();
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus();
  const [activeSource, setActiveSource] = useState<LogSource>("proxy");
  const { lines, connected, clear } = useLogs(activeSource);
  const slots = useSlots();
  const [modelName, setModelName] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then((cfg) => setModelName(cfg.models[0]?.name ?? null)).catch(() => {});
  }, []);

  const serverLabel = modelName ?? "Llama Server 1";
  const logHeader = activeSource === "proxy" ? "Proxy Server" : serverLabel;

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-4">Logs</h1>
      <div className="flex gap-6 items-start mb-4">
        <div className="space-y-4">
          <ProxyStatusCard
            status={proxyStatus}
            onClick={() => setActiveSource("proxy")}
            selected={activeSource === "proxy"}
          />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        <div className="space-y-4">
          <ServerStatusCard
            name={serverLabel}
            status={status}
            slots={slots}
            onClick={() => setActiveSource("model-0")}
            selected={activeSource === "model-0"}
          />
          <ServerControls status={status} onAction={refresh} />
        </div>
      </div>
      <h2 className="text-lg font-semibold mb-2">{logHeader} Logs</h2>
      <div className="flex-1 min-h-0">
        <LogViewer lines={lines} connected={connected} onClear={clear} source={activeSource} />
      </div>
    </div>
  );
}
