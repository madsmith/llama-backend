import { useState, useRef, useCallback, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";
import type { ServerStatus, SlotInfo, HealthStatus } from "../api/types";
import { api } from "../api/client";
import { getWsV2 } from "../api/wsv2";

const stateColors: Record<string, string> = {
  unknown: "bg-yellow-500",
  stopped: "bg-gray-600",
  starting: "bg-yellow-500",
  running: "bg-green-500",
  stopping: "bg-yellow-500",
  error: "bg-red-500",
  remote: "bg-blue-500",
};

const stateLabels: Record<string, string> = {
  unknown: "Unknown",
  stopped: "Stopped",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  error: "Error",
  remote: "Remote",
};

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (d > 0) return `${d}d ${h}h ${m}m ${s}s`;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtFloat(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

function slotDetail(s: SlotInfo): string {
  if (s.prompt_progress != null) {
    return `Prompt ${Math.round(s.prompt_progress * 100)}%`;
  }

  const nt = s.next_token?.[0];

  if (!nt?.has_next_token) return "processing prompt";

  const parts: string[] = [];
  if (nt?.n_decoded != null) parts.push(`${nt.n_decoded} tokens`);
  if (nt?.n_remain != null && nt.n_remain !== -1)
    parts.push(`${nt.n_remain} left`);
  return parts.length > 0 ? parts.join(" · ") : "generating";
}

function slotTooltipParams(s: SlotInfo): [string, string][] {
  if (!s.is_processing) return [];
  const entries: [string, string][] = [];
  if (s.prompt_n_processed != null && s.prompt_n_total != null)
    entries.push([
      "prompt",
      `${s.prompt_n_processed} / ${s.prompt_n_total} tokens`,
    ]);
  if (s.params) {
    if (s.params.temperature != null)
      entries.push(["temperature", fmtFloat(s.params.temperature)]);
    if (s.params.top_p != null)
      entries.push(["top_p", fmtFloat(s.params.top_p)]);
    if (s.params.min_p != null)
      entries.push(["min_p", fmtFloat(s.params.min_p)]);
    if (s.params.chat_format) entries.push(["format", s.params.chat_format]);
  }
  return entries;
}

const TOOLTIP_OFFSET = { x: 12, y: 16 };

function SlotRow({ slot, modelSuid }: { slot: SlotInfo; modelSuid: string }) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const showTimer = useRef<ReturnType<typeof setTimeout>>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(null);

  const params = slotTooltipParams(slot);

  const onEnter = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    if (params.length === 0) return;
    showTimer.current = setTimeout(() => setVisible(true), 300);
  }, [params.length]);

  const onLeave = useCallback(() => {
    if (showTimer.current) clearTimeout(showTimer.current);
    hideTimer.current = setTimeout(() => setVisible(false), 150);
  }, []);

  const onMove = useCallback((e: React.MouseEvent) => {
    setPos({ x: e.clientX, y: e.clientY });
  }, []);

  return (
    <div
      className="flex items-baseline gap-2 text-sm"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onMouseMove={onMove}
    >
      {slot.is_processing && slot.cancellable ? (
        <button
          title="Cancel inference"
          onClick={(e) => {
            e.stopPropagation();
            api.cancelSlot(modelSuid, slot.id).catch(() => {});
          }}
          className="shrink-0 h-2 w-2 rounded-full -translate-y-px bg-yellow-500 hover:bg-red-500 cursor-pointer transition-colors"
        />
      ) : (
        <span
          className={`shrink-0 inline-block h-2 w-2 rounded-full -translate-y-px ${slot.is_processing ? "bg-yellow-500" : "bg-green-500"}`}
        />
      )}
      <Link
        to={`/${modelSuid}/slots`}
        onClick={(e) => e.stopPropagation()}
        className="text-gray-300 hover:text-white w-14 shrink-0 transition"
      >
        Slot {slot.id}
      </Link>
      <span
        className={`w-10 shrink-0 ${slot.is_processing ? "text-yellow-400" : "text-green-400"}`}
      >
        {slot.is_processing ? "Busy" : "Idle"}
      </span>
      <span className="text-xs font-mono truncate text-gray-300">
        {slot.is_processing ? slotDetail(slot) : "ready"}
      </span>

      {visible && params.length > 0 && (
        <div
          className="fixed z-50 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 shadow-lg whitespace-nowrap pointer-events-none"
          style={{
            left: pos.x + TOOLTIP_OFFSET.x,
            top: pos.y + TOOLTIP_OFFSET.y,
          }}
        >
          <table className="text-xs">
            <tbody>
              {params.map(([k, v]) => (
                <tr key={k}>
                  <td className="pr-3 text-gray-400">{k}</td>
                  <td className="font-mono text-gray-200">{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function isLoopback(url: string): boolean {
  try {
    const { hostname } = new URL(url);
    return (
      hostname === "localhost" ||
      hostname === "0.0.0.0" ||
      hostname === "::1" ||
      hostname === "[::1]" ||
      /^127\./.test(hostname)
    );
  } catch {
    return false;
  }
}

const urlClass = "hover:text-gray-200 transition";

function ServerUrl({
  displayUrl,
  allowProxy,
  proxyUrl,
}: {
  displayUrl: string;
  allowProxy: boolean;
  proxyUrl: string | undefined;
}) {
  const stopProp = (e: React.MouseEvent) => e.stopPropagation();
  if (allowProxy && proxyUrl) {
    return (
      <a href={proxyUrl} target="_blank" rel="noreferrer" onClick={stopProp} className={urlClass}>
        {displayUrl}
      </a>
    );
  }
  if (!allowProxy && isLoopback(displayUrl)) {
    return <span>{displayUrl}</span>;
  }
  return (
    <a href={displayUrl} target="_blank" rel="noreferrer" onClick={stopProp} className={urlClass}>
      {displayUrl}
    </a>
  );
}

function remoteDisplay(health: HealthStatus | null): {
  label: string;
  color: string;
} {
  if (health == null) return { label: "Offline", color: "bg-red-500" };
  if (health.status === "ok")
    return { label: "Running", color: "bg-green-500" };
  if (health.status === "unknown")
    return { label: "Unknown", color: "bg-yellow-500" };
  return { label: health.status, color: "bg-red-500" };
}

interface Props {
  name: string;
  modelSuid?: string;
  status: ServerStatus;
  onClick?: () => void;
  selected?: boolean;
  remoteAddress?: string;
  health?: HealthStatus | null;
  allowProxy?: boolean;
  proxyBaseUrl?: string;
}

export default function ServerStatusCard({
  name,
  modelSuid,
  status,
  onClick,
  selected,
  remoteAddress,
  health,
  allowProxy,
  proxyBaseUrl,
}: Props) {
  const navigate = useNavigate();
  const [slots, setSlots] = useState<SlotInfo[]>([]);

  // Initial slot fetch + push updates.
  const handleSlotResponse = useCallback(
    (msg: Record<string, unknown>) => {
      if (msg.suid !== modelSuid) return;
      setSlots((msg.slots as SlotInfo[]) ?? []);
    },
    [modelSuid],
  );

  const requestSlots = useCallback(
    () => modelSuid && getWsV2().send({ msg: "slot_status", suid: modelSuid }),
    [modelSuid],
  );

  useEffect(
    () => getWsV2().subscribe("slot_status_response", handleSlotResponse, requestSlots),
    [handleSlotResponse, requestSlots],
  );

  useEffect(
    () => {
      if (!modelSuid) return;
      return getWsV2().subscribeToEvent("slots", modelSuid, (data) => {
        setSlots((data.slots as SlotInfo[]) ?? []);
      });
    },
    [modelSuid],
  );

  // Fetch slots immediately when server becomes running or remote.
  useEffect(() => {
    if (status.state === "running" || status.state === "remote") {
      requestSlots();
    }
  }, [status.state, requestSlots]);

  // Clear slots when server stops.
  useEffect(() => {
    if (status.state !== "running" && status.state !== "remote") {
      setSlots([]);
    }
  }, [status.state]);

  const isRemote = status.state === "remote";
  const remote = isRemote ? remoteDisplay(health ?? null) : null;
  return (
    <div
      className={`w-96 min-h-55 rounded-xl border border-gray-800 bg-gray-900/75 hover:bg-gray-800/60 pt-5 px-5 pb-3 flex flex-col transition-colors ${selected ? "ring-2 ring-blue-500" : ""} ${onClick ? "cursor-pointer" : ""}`}
      onClick={onClick}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium tracking-wide text-gray-400">
          {name}
        </span>
        <button
          title="Properties"
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/${modelSuid}/properties`);
          }}
          className="text-gray-600 hover:text-gray-300 transition text-sm leading-none"
        >
          ⓘ
        </button>
      </div>
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-2.5">
          <span
            className={`inline-block h-3 w-3 rounded-full -translate-y-px ${isRemote ? remote!.color : (stateColors[status.state] ?? "bg-gray-600")}`}
          />
          <span className="text-lg font-semibold">
            {isRemote
              ? remote!.label
              : (stateLabels[status.state] ?? status.state)}
          </span>
        </div>
        <span className="text-sm text-gray-400 font-mono">
          {isRemote ? (
            remoteAddress ? (
              <ServerUrl
                displayUrl={remoteAddress}
                allowProxy={allowProxy ?? true}
                proxyUrl={proxyBaseUrl && modelSuid ? `${proxyBaseUrl}/proxy/${modelSuid}/` : undefined}
              />
            ) : (
              "—"
            )
          ) : status.host != null && status.port != null ? (
            <ServerUrl
              displayUrl={`http://${status.host}:${status.port}`}
              allowProxy={allowProxy ?? true}
              proxyUrl={proxyBaseUrl && modelSuid ? `${proxyBaseUrl}/proxy/${modelSuid}/` : undefined}
            />
          ) : (
            "—"
          )}
        </span>
      </div>

      {slots.length > 0 && (
        <div className="mt-4 ml-5 space-y-1.5">
          {slots.map((s) => (
            <SlotRow key={s.id} slot={s} modelSuid={modelSuid ?? ""} />
          ))}
        </div>
      )}

      <div className="mt-auto pt-2 flex justify-between text-xs text-gray-600 leading-none">
        {isRemote ? (
          <>
            <span />
            <span>Remote</span>
          </>
        ) : (
          <>
            <span>
              Uptime:{" "}
              {status.uptime != null ? formatUptime(status.uptime) : "—"}
            </span>
            <span>PID {status.pid ?? "—"}</span>
          </>
        )}
      </div>
    </div>
  );
}
