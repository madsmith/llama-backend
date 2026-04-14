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

function ModelLogCard({ modelSuid, name, selected, path, navigate, allowProxy, proxyBaseUrl }: {
  modelSuid: string;
  name: string;
  selected: boolean;
  path: string;
  navigate: (path: string) => void;
  allowProxy: boolean;
  proxyBaseUrl: string;
}) {
  const { status, refresh } = useServerStatusWS(modelSuid);
  const statusOrUnknown = status ?? { state: "unknown" as const, pid: null, host: null, port: null, uptime: null };

  return (
    <div className="space-y-4">
      <ServerStatusCard
        name={name}
        modelSuid={modelSuid}
        status={statusOrUnknown}
        onClick={() => navigate(path)}
        selected={selected}
        allowProxy={allowProxy}
        proxyBaseUrl={proxyBaseUrl}
      />
      <ServerControls status={statusOrUnknown} modelSuid={modelSuid} onAction={refresh} />
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

  const didInitialScroll = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Use instant scroll on page load, smooth when the user switches cards
    const behavior = didInitialScroll.current ? "smooth" : "instant";

    const tryScroll = () => {
      const selected = el.querySelector(`[data-source="${sourceKey}"]`);
      if (!selected) return false;
      selected.scrollIntoView({ inline: "nearest", behavior, block: "nearest" });
      didInitialScroll.current = true;
      return true;
    };

    if (tryScroll()) return;

    // Element not yet in the DOM (config still loading) — watch for it
    const observer = new MutationObserver(() => {
      if (tryScroll()) observer.disconnect();
    });
    observer.observe(el, { childList: true, subtree: true });
    return () => observer.disconnect();
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

export default function Logs() {
  const { modelSuid } = useParams<{ modelSuid?: string }>();
  const navigate = useNavigate();
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const poll = pollRatesFromConfig(config);
  const { status: proxyStatus, refresh: refreshProxy } = useProxyStatus(poll.proxyStatus);

  const isProxy = !modelSuid || modelSuid === "proxy";
  const logSuid = isProxy ? undefined : modelSuid;

  const { lines, connected, clear, isPending, hasMore, isLoadingMore, loadMore } = useLogs(
    isProxy ? "proxy" : "server",
    logSuid,
  );

  useEffect(() => { document.title = "Llama Manager - Logs"; }, []);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const remotes = useRemotes();
  const models = config?.models ?? [];
  const localModels = models
    .map((m, i) => ({ model: m, index: i }))
    .filter(({ model }) => (model.type ?? "local") !== "remote");

  const allRemoteModels = remotes.flatMap(rm => rm.models);

  const logHeader = isProxy
    ? "Proxy Server"
    : localModels.find(({ model: m }) => m.suid === modelSuid)?.model.name
      ?? allRemoteModels.find(m => m.suid === modelSuid)?.name
      ?? "Server";

  // Redirect to proxy logs if current source is a config-level remote model (type="remote")
  useEffect(() => {
    if (!isProxy && modelSuid) {
      const localModel = models.find(m => m.suid === modelSuid);
      if (localModel && (localModel.type ?? "local") === "remote") {
        navigate("/logs/proxy", { replace: true });
      }
    }
  }, [isProxy, modelSuid, models, navigate]);

  const proxyBaseUrl = `http://${window.location.hostname}:${config?.api_server.port ?? 1234}`;
  const sourceKey = logSuid ?? "proxy";

  return (
    <div className="flex flex-col h-full">
      <h1 className="text-2xl font-bold mb-4">Logs</h1>
      <ScrollStrip sourceKey={sourceKey}>
        <div data-source="proxy" className="space-y-4">
          <ProxyStatusCard
            status={proxyStatus}
            onClick={() => navigate("/logs/proxy")}
            selected={isProxy}
          />
          <ProxyControls status={proxyStatus} onAction={refreshProxy} />
        </div>
        {localModels.map(({ model: m, index: i }) => (
          <div key={m.suid} data-source={m.suid}>
            <ModelLogCard
              modelSuid={m.suid}
              name={m.name ?? `Llama Server ${i + 1}`}
              selected={!isProxy && modelSuid === m.suid}
              path={`/logs/${m.suid}`}
              navigate={navigate}
              allowProxy={m.allow_proxy ?? true}
              proxyBaseUrl={proxyBaseUrl}
            />
          </div>
        ))}
        {remotes.flatMap((rm) =>
          rm.models.map((m) => (
            <div key={m.suid} data-source={m.suid}>
              <ModelLogCard
                modelSuid={m.suid}
                name={m.name ?? "Remote Model"}
                selected={!isProxy && modelSuid === m.suid}
                path={`/logs/${m.suid}`}
                navigate={navigate}
                allowProxy={m.allow_proxy ?? true}
                proxyBaseUrl={proxyBaseUrl}
              />
            </div>
          ))
        )}
      </ScrollStrip>
      <h2 className="text-lg font-semibold mb-2">{logHeader} Logs</h2>
      <div className="flex-1 min-h-0">
        <LogViewer key={sourceKey} lines={lines} connected={connected} onClear={clear} source={sourceKey} isPending={isPending} hasMore={hasMore} isLoadingMore={isLoadingMore} onLoadMore={loadMore} />
      </div>
    </div>
  );
}
