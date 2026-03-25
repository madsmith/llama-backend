import { useState } from "react";
import { api } from "../api/client";
import type { ProxyStatus } from "../api/types";

interface Props {
  status: ProxyStatus;
  onAction: () => void;
}

export default function ProxyControls({ status, onAction }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onAction();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      onAction();
    } finally {
      setBusy(false);
    }
  };

  const isUnknown = status.state === "unknown";
  const isStopped = status.state === "stopped";
  const isRunning = status.state === "running";

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          disabled={busy || isUnknown || !isStopped}
          onClick={() => act(() => api.proxyStart())}
          className="rounded-md bg-green-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Start
        </button>
        <button
          disabled={busy || isUnknown || !isRunning}
          onClick={() => act(() => api.proxyStop())}
          className="rounded-md bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Stop
        </button>
        <button
          disabled={busy || !isRunning}
          onClick={() => act(() => api.proxyRestart())}
          className="rounded-md bg-yellow-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-yellow-600 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Restart
        </button>
      </div>
      {error && (
        <div className="rounded-md bg-red-900/50 border border-red-700 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}
    </div>
  );
}
