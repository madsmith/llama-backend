import type { ProxyStatus } from "../api/types";

const stateColors: Record<string, string> = {
  stopped: "bg-gray-600",
  running: "bg-green-500",
};

const stateLabels: Record<string, string> = {
  stopped: "Stopped",
  running: "Running",
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

interface Props {
  status: ProxyStatus;
  onClick?: () => void;
  selected?: boolean;
}

export default function ProxyStatusCard({ status, onClick, selected }: Props) {
  return (
    <div
      className={`w-96 min-h-[220px] rounded-xl border border-gray-800 bg-gray-900 pt-5 px-5 pb-3 flex flex-col ${selected ? "ring-2 ring-blue-500" : ""} ${onClick ? "cursor-pointer" : ""}`}
      onClick={onClick}
    >
      <div className="text-xs font-medium tracking-wide text-gray-400 mb-2">
        Proxy Server
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

      <div className="mt-auto pt-2 flex justify-between text-xs text-gray-600 leading-none">
        <span>Uptime: {status.uptime != null ? formatUptime(status.uptime) : "—"}</span>
        <span>PID {status.pid ?? "—"}</span>
      </div>
    </div>
  );
}
