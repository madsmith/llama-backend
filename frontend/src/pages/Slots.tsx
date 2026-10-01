import { useState, useCallback, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SlotInfo } from "../api/types";
import JsonTree from "../components/JsonTree";

export default function Slots() {
  const { modelSuid } = useParams<{ modelSuid: string }>();
  const navigate = useNavigate();
  const [slots, setSlots] = useState<SlotInfo[] | null>(null);

  useEffect(() => { document.title = "Llama Manager - Slots"; }, []);
  const [loading, setLoading] = useState(Boolean(modelSuid));
  const [error, setError] = useState("");
  const [modelName, setModelName] = useState<string | null>(null);

  useEffect(() => {
    if (!modelSuid) return;
    api.getConfig().then((cfg) => {
      const m = cfg.models.find(m => m.suid === modelSuid);
      setModelName(m?.name ?? null);
    }).catch(() => {});
  }, [modelSuid]);

  // Only sets state from async callbacks, so it is safe to call from an effect.
  const load = useCallback(() => {
    if (!modelSuid) return;
    api
      .getSlots(modelSuid)
      .then((s) => {
        setSlots(s);
        setError("");
      })
      .catch(() => {
        setSlots(null);
        setError("Could not fetch slots. Is the server running?");
      })
      .finally(() => setLoading(false));
  }, [modelSuid]);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = () => {
    setLoading(true);
    setError("");
    load();
  };

  const title = `${modelName ?? "Server"} Slots`;

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
