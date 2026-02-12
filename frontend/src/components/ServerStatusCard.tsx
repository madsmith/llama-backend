import { useState, useRef, useCallback } from "react";
import type { ServerStatus, SlotInfo } from "../api/types";

const stateColors: Record<string, string> = {
  stopped: "bg-gray-600",
  starting: "bg-yellow-500",
  running: "bg-green-500",
  stopping: "bg-yellow-500",
  error: "bg-red-500",
};

const stateLabels: Record<string, string> = {
  stopped: "Stopped",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  error: "Error",
};

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtFloat(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

function slotDetail(s: SlotInfo): string {
  const nt = s.next_token?.[0];
  const parts: string[] = [];

  if (nt?.n_decoded != null) parts.push(`${nt.n_decoded} tokens`);
  if (nt?.n_remain != null && nt.n_remain !== -1) parts.push(`${nt.n_remain} left`);

  return parts.length > 0 ? parts.join(" · ") : "processing";
}

function slotTooltipParams(s: SlotInfo): [string, string][] {
  if (!s.is_processing || !s.params) return [];
  const entries: [string, string][] = [];
  if (s.params.temperature != null) entries.push(["temperature", fmtFloat(s.params.temperature)]);
  if (s.params.top_p != null) entries.push(["top_p", fmtFloat(s.params.top_p)]);
  if (s.params.min_p != null) entries.push(["min_p", fmtFloat(s.params.min_p)]);
  if (s.params.chat_format) entries.push(["format", s.params.chat_format]);
  return entries;
}

const TOOLTIP_OFFSET = { x: 12, y: 16 };

function SlotRow({ slot }: { slot: SlotInfo }) {
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
      <span
        className={`shrink-0 inline-block h-2 w-2 rounded-full translate-y-[-1px] ${slot.is_processing ? "bg-yellow-500" : "bg-green-500"}`}
      />
      <span className="text-gray-300 w-14 shrink-0">Slot {slot.id}</span>
      <span className={`w-10 shrink-0 ${slot.is_processing ? "text-yellow-400" : "text-green-400"}`}>
        {slot.is_processing ? "Busy" : "Idle"}
      </span>
      <span className="text-xs font-mono truncate text-gray-300">
        {slot.is_processing ? slotDetail(slot) : "ready"}
      </span>

      {visible && params.length > 0 && (
        <div
          className="fixed z-50 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 shadow-lg whitespace-nowrap pointer-events-none"
          style={{ left: pos.x + TOOLTIP_OFFSET.x, top: pos.y + TOOLTIP_OFFSET.y }}
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

interface Props {
  name: string;
  status: ServerStatus;
  slots: SlotInfo[];
}

export default function ServerStatusCard({ name, status, slots }: Props) {
  return (
    <div className="w-96 min-h-[220px] rounded-xl border border-gray-800 bg-gray-900 pt-5 px-5 pb-3 flex flex-col">
      <div className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-2">
        {name}
      </div>
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-2.5">
          <span
            className={`inline-block h-3 w-3 rounded-full translate-y-[-1px] ${stateColors[status.state] ?? "bg-gray-600"}`}
          />
          <span className="text-lg font-semibold">
            {stateLabels[status.state] ?? status.state}
          </span>
        </div>
        <span className="text-sm text-gray-400 font-mono">
          {status.host != null && status.port != null
            ? `http://${status.host}:${status.port}`
            : "—"}
        </span>
      </div>

      {slots.length > 0 && (
        <div className="mt-4 ml-5 space-y-1.5">
          {slots.map((s) => (
            <SlotRow key={s.id} slot={s} />
          ))}
        </div>
      )}

      <div className="mt-auto pt-2 flex justify-between text-xs text-gray-600 leading-none">
        <span>Uptime: {status.uptime != null ? formatUptime(status.uptime) : "—"}</span>
        <span>PID {status.pid ?? "—"}</span>
      </div>
    </div>
  );
}
