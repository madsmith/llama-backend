import { useState } from "react";
import { getWsV2 } from "../api/wsv2";
import type { ServerStatus } from "../api/types";

interface Props {
  status: ServerStatus | null;
  serverId: string;
  modelSuid: number;
  onAction: () => void;
}

export default function ServerControls({ status, serverId, modelSuid, onAction }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (operation: "start" | "stop" | "restart") => {
    setBusy(true);
    setError(null);
    try {
      const ws = getWsV2();
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          unsub();
          reject(new Error("Timeout waiting for server response"));
        }, 10000);
        let unsub: () => void;
        unsub = ws.subscribe("server_control_response", (msg) => {
          if (msg.server_id !== serverId) return;
          clearTimeout(timeout);
          unsub();
          if (msg.success) {
            resolve();
          } else {
            reject(new Error((msg.error as string | undefined) ?? "Operation failed"));
          }
        });
        ws.send({ msg: "server_control", operation, server_id: serverId, model_suid: modelSuid });
      });
      onAction();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      onAction();
    } finally {
      setBusy(false);
    }
  };

  const isUnknown = !status || status.state === "unknown";
  const isStopped = status?.state === "stopped" || status?.state === "error";
  const isRunning = status?.state === "running";

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          disabled={busy || isUnknown || !isStopped}
          onClick={() => act("start")}
          className="rounded-md bg-green-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Start
        </button>
        <button
          disabled={busy || isUnknown || !isRunning}
          onClick={() => act("stop")}
          className="rounded-md bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Stop
        </button>
        <button
          disabled={busy || isUnknown || !isRunning}
          onClick={() => act("restart")}
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
