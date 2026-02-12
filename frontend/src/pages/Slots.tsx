import { useState, useCallback, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SlotInfo } from "../api/types";
import JsonTree from "../components/JsonTree";

export default function Slots() {
  const { modelIndex } = useParams<{ modelIndex: string }>();
  const idx = Number(modelIndex ?? 0);
  const navigate = useNavigate();
  const [slots, setSlots] = useState<SlotInfo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [modelName, setModelName] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then((cfg) => {
      const m = cfg.models[idx];
      setModelName(m?.name ?? null);
    }).catch(() => {});
  }, [idx]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .getSlots()
      .then(setSlots)
      .catch(() => {
        setSlots(null);
        setError("Could not fetch slots. Is the server running?");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const title = `${modelName ?? `Llama Server ${idx + 1}`} Slots`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">{title}</h1>
          <button
            onClick={refresh}
            disabled={loading}
            className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700 disabled:opacity-40 transition"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
        <button
          onClick={() => navigate(-1)}
          className="rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700 transition"
        >
          Back
        </button>
      </div>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {!slots && !error && !loading && (
        <p className="text-sm text-gray-500">No slot data available.</p>
      )}

      {slots && (
        <div className="overflow-auto rounded-xl border border-gray-800 bg-gray-900 p-5">
          <JsonTree data={slots} defaultExpandDepth={2} />
        </div>
      )}
    </div>
  );
}
