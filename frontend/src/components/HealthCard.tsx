import type { HealthStatus } from "../api/types";

interface Props {
  health: HealthStatus | null;
}

export default function HealthCard({ health }: Props) {
  const ok = health?.status === "ok" || health?.status === "no slot available";
  const label = health?.status ?? "unavailable";

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-5">
      <h3 className="mb-3 text-sm font-semibold text-gray-400 uppercase tracking-wider">
        Health
      </h3>
      <div className="flex items-center gap-3">
        <span
          className={`h-4 w-4 rounded-full ${ok ? "bg-green-500" : "bg-red-500"}`}
        />
        <span className="text-lg font-medium capitalize">{label}</span>
      </div>
      {health && (health.slots_idle != null || health.slots_processing != null) && (
        <div className="mt-3 text-sm text-gray-400">
          Idle: {health.slots_idle ?? "?"} &middot; Processing:{" "}
          {health.slots_processing ?? "?"}
        </div>
      )}
    </div>
  );
}
