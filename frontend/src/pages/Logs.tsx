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

function ModelLogCard({ modelIndex, serverId, name, source, navigate }: { modelIndex: number; serverId: string | undefined; name: string; source: string; navigate: (path: string) => void }) {
  const { status, refresh } = useServerStatusWS(serverId);

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelIndex={modelIndex}
        status={status}
        onClick={() => navigate(`/logs/${modelIndex}`)}
        selected={source === String(modelIndex)}
      />
      <ServerControls status={status} modelIndex={modelIndex} onAction={refresh} />
    </div>
  );
}

function ScrollStrip({ children, source }: { children: React.ReactNode; source: string }) {
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
    const selected = el.querySelector(`[data-source="${source}"]`);
    if (selected) {
      selected.scrollIntoView({ inline: "nearest", behavior: "smooth", block: "nearest" });
    }
  }, [source]);

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

export default function Logs() {
  const { source = "proxy" } = useParams<{ source: string }>();
  const navigate = useNavigate();
  const wsSource = source === "proxy" ? "proxy" : `model-${source}`;
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const poll = pollRatesFromConfig(config);
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus(poll.proxyStatus);
  const { lines, connected, clear } = useLogs(wsSource);

  useEffect(() => { document.title = "Llama Manager - Logs"; }, []);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const remotes = useRemotes();

  // Build a map from local_index -> display name for remote-manager-proxied models
  const remoteModelNames = new Map<number, string>();
  for (const rm of remotes) {
    for (const m of rm.models) {
      remoteModelNames.set(
        m.local_index,
        m.name ?? `Remote Model ${m.remote_model_index + 1}`,
      );
    }
  }

  const models = config?.models ?? [];
  const modelIndex = source === "proxy" ? null : Number(source);

  const logHeader = source === "proxy"
    ? "Proxy Server"
    : remoteModelNames.get(modelIndex!) ??
      models[modelIndex!]?.name ??
      `Llama Server ${(modelIndex ?? 0) + 1}`;

  // Redirect to proxy logs if current source is a config-level remote model (type="remote")
  useEffect(() => {
    if (modelIndex != null && models.length > 0 && modelIndex < models.length) {
      if ((models[modelIndex].type ?? "local") === "remote") {
        navigate("/logs/proxy", { replace: true });
      }
    }
  }, [modelIndex, models, navigate]);

  // Only show local models and remote-manager-proxied models in the card list
  const localModels = models
    .map((m, i) => ({ model: m, index: i }))
    .filter(({ model }) => (model.type ?? "local") !== "remote");

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-4">Logs</h1>
      <ScrollStrip source={source}>
        <div data-source="proxy" className="space-y-4">
          <ProxyStatusCard
            status={proxyStatus}
            onClick={() => navigate("/logs/proxy")}
            selected={source === "proxy"}
          />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        {localModels.map(({ model: m, index: i }) => (
          <div key={i} data-source={String(i)}>
            <ModelLogCard
              modelIndex={i}
              serverId={config?.manager_id ? `${config.manager_id}:model-${i}` : undefined}
              name={m.name ?? `Llama Server ${i + 1}`}
              source={source}
              navigate={navigate}
            />
          </div>
        ))}
        {remotes.flatMap((rm) =>
          rm.models.map((m) => (
            <div key={m.local_index} data-source={String(m.local_index)}>
              <ModelLogCard
                modelIndex={m.local_index}
                serverId={m.server_id}
                name={m.name ?? `Remote Model ${m.remote_model_index + 1}`}
                source={source}
                navigate={navigate}
              />
            </div>
          )),
        )}
      </ScrollStrip>
      <h2 className="text-lg font-semibold mb-2">{logHeader} Logs</h2>
      <div className="flex-1 min-h-0">
        <LogViewer lines={lines} connected={connected} onClear={clear} source={wsSource} />
      </div>
    </div>
  );
}
