import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ServerConfig } from "../api/types";
import ServerStatusCard from "../components/ServerStatusCard";
import ServerControls from "../components/ServerControls";
import ProxyStatusCard from "../components/ProxyStatusCard";
import ProxyControls from "../components/ProxyControls";
import LogViewer from "../components/LogViewer";
import { useProxyStatus, useServerStatusWS, useLogs, useRemotes, pollRatesFromConfig } from "../api/hooks";

function ModelLogCard({ modelIndex, serverId, name, selected, path, navigate }: {
  modelIndex: number;
  serverId: string;
  name: string;
  selected: boolean;
  path: string;
  navigate: (path: string) => void;
}) {
  const { status, refresh } = useServerStatusWS(serverId);
  const statusOrUnknown = status ?? { state: "unknown" as const, pid: null, host: null, port: null, uptime: null };

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelIndex={modelIndex}
        status={statusOrUnknown}
        onClick={() => navigate(path)}
        selected={selected}
      />
      <ServerControls status={statusOrUnknown} serverId={serverId} modelSuid={modelIndex} onAction={refresh} />
    </div>
  );
}

function ScrollStrip({ children, sourceKey }: { children: React.ReactNode; sourceKey: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 4);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  }, []);

  useEffect(() => {
    updateScroll();
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(updateScroll);
    observer.observe(el);
    return () => observer.disconnect();
  }, [updateScroll]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const selected = el.querySelector(`[data-source="${sourceKey}"]`);
    if (selected) {
      selected.scrollIntoView({ inline: "nearest", behavior: "smooth", block: "nearest" });
    }
  }, [sourceKey]);

  const scroll = (dir: number) => {
    ref.current?.scrollBy({ left: dir * 408, behavior: "smooth" });
  };

  return (
    <div className="relative mb-4">
      <div
        ref={ref}
        onScroll={updateScroll}
        className="flex gap-6 items-start overflow-x-auto scrollbar-hide p-2 -m-2 scroll-p-2"
      >
        {children}
      </div>
      {canScrollLeft && (
        <button
          onClick={() => scroll(-1)}
          className="absolute left-0 top-[1em] h-[188px] w-8 flex items-center justify-center bg-black/30 hover:bg-black/50 transition-colors cursor-pointer rounded-r-lg"
        >
          <span className="text-white text-lg">&lsaquo;</span>
        </button>
      )}
      {canScrollRight && (
        <button
          onClick={() => scroll(1)}
          className="absolute right-0 top-[1em] h-[188px] w-8 flex items-center justify-center bg-black/30 hover:bg-black/50 transition-colors cursor-pointer rounded-l-lg"
        >
          <span className="text-white text-lg">&rsaquo;</span>
        </button>
      )}
    </div>
  );
}

type LogMode =
  | { type: "proxy" }
  | { type: "local"; index: number }
  | { type: "remote"; serverId: string; remoteIndex: number };

export default function Logs() {
  const { modelIndex, serverId, remoteIndex } = useParams<{
    modelIndex?: string;
    serverId?: string;
    remoteIndex?: string;
  }>();
  const navigate = useNavigate();
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const poll = pollRatesFromConfig(config);
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus(poll.proxyStatus);

  const logMode: LogMode = serverId != null && remoteIndex != null
    ? { type: "remote", serverId, remoteIndex: Number(remoteIndex) }
    : modelIndex != null && modelIndex !== "proxy"
    ? { type: "local", index: Number(modelIndex) }
    : { type: "proxy" };

  const sourceKey = logMode.type === "proxy" ? "proxy"
    : logMode.type === "local" ? String(logMode.index)
    : `${logMode.serverId}/${logMode.remoteIndex}`;

  const logServerId = logMode.type === "local" && config?.manager_id
    ? `${config.manager_id}:model-${logMode.index}`
    : logMode.type === "remote" ? logMode.serverId
    : undefined;

  const { lines, connected, clear } = useLogs(
    logMode.type === "proxy" ? "proxy" : "server",
    logServerId,
  );

  useEffect(() => { document.title = "Llama Manager - Logs"; }, []);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const remotes = useRemotes();

  const models = config?.models ?? [];

  const logHeader = logMode.type === "proxy"
    ? "Proxy Server"
    : logMode.type === "local"
    ? models[logMode.index]?.name ?? `Llama Server ${logMode.index + 1}`
    : remotes.flatMap(rm => rm.models).find(
        m => m.server_id === logMode.serverId && m.remote_model_index === logMode.remoteIndex
      )?.name ?? `Remote Model ${logMode.remoteIndex + 1}`;

  // Redirect to proxy logs if current source is a config-level remote model (type="remote")
  useEffect(() => {
    if (logMode.type === "local" && models.length > 0 && logMode.index < models.length) {
      if ((models[logMode.index].type ?? "local") === "remote") {
        navigate("/logs/proxy", { replace: true });
      }
    }
  }, [logMode, models, navigate]);

  // Only show local models in the card list (not config-level remote models)
  const localModels = models
    .map((m, i) => ({ model: m, index: i }))
    .filter(({ model }) => (model.type ?? "local") !== "remote");

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-4">Logs</h1>
      <ScrollStrip sourceKey={sourceKey}>
        <div data-source="proxy" className="space-y-4">
          <ProxyStatusCard
            status={proxyStatus}
            onClick={() => navigate("/logs/proxy")}
            selected={logMode.type === "proxy"}
          />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        {localModels.map(({ model: m, index: i }) => (
          <div key={i} data-source={String(i)}>
            <ModelLogCard
              modelIndex={i}
              serverId={config?.manager_id ?? ""}
              name={m.name ?? `Llama Server ${i + 1}`}
              selected={logMode.type === "local" && logMode.index === i}
              path={`/logs/${i}`}
              navigate={navigate}
            />
          </div>
        ))}
        {remotes.flatMap((rm) =>
          rm.models.map((m) => {
            const remoteSourceKey = `${m.server_id}/${m.remote_model_index}`;
            return (
              <div key={remoteSourceKey} data-source={remoteSourceKey}>
                <ModelLogCard
                  modelIndex={m.remote_model_index}
                  serverId={m.server_id}
                  name={m.name ?? `Remote Model ${m.remote_model_index + 1}`}
                  selected={logMode.type === "remote" && logMode.serverId === m.server_id && logMode.remoteIndex === m.remote_model_index}
                  path={`/logs/${m.server_id}/${m.remote_model_index}`}
                  navigate={navigate}
                />
              </div>
            );
          })
        )}
      </ScrollStrip>
      <h2 className="text-lg font-semibold mb-2">{logHeader} Logs</h2>
      <div className="flex-1 min-h-0">
        <LogViewer lines={lines} connected={connected} onClear={clear} source={sourceKey} />
      </div>
    </div>
  );
}
