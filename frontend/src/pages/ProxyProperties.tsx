import { useState, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import JsonTree from "../components/JsonTree";

const buttonClass =
  "rounded-md bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-gray-700 disabled:opacity-40 transition";

export default function ProxyProperties() {
  const navigate = useNavigate();
  const [modelsUrl, setModelsUrl] = useState<string | null>(null);
  const [models, setModels] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => { document.title = "Llama Manager - Proxy Properties"; }, []);

  // Same address the dashboard links model servers through: the proxy port
  // on the host the UI was loaded from.
  useEffect(() => {
    api.getConfig()
      .then((cfg) => setModelsUrl(`http://${window.location.hostname}:${cfg.api_server.port}/v1/models`))
      .catch(() => {
        setError("Could not load the config.");
        setLoading(false);
      });
  }, []);

  // Only sets state from async callbacks, so it is safe to call from an effect.
  const load = useCallback(() => {
    if (!modelsUrl) return;
    fetch(modelsUrl)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        setModels(await res.json());
        setError("");
      })
      .catch((e: unknown) => {
        setModels(null);
        setError(`Could not fetch ${modelsUrl}: ${e instanceof Error ? e.message : String(e)}. Is the proxy running?`);
      })
      .finally(() => setLoading(false));
  }, [modelsUrl]);

  useEffect(() => {
    load();
  }, [load]);

  const refresh = () => {
    setLoading(true);
    setError("");
    load();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold">Proxy Server Properties</h1>
          <button onClick={refresh} disabled={loading || !modelsUrl} className={buttonClass}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
        <button onClick={() => navigate(-1)} className={buttonClass}>
          Back
        </button>
      </div>

      {modelsUrl && (
        <p className="text-sm text-gray-400">
          Models:{" "}
          <a href={modelsUrl} target="_blank" rel="noreferrer" className="font-mono hover:text-gray-200 transition">
            {modelsUrl}
          </a>
        </p>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {models != null && (
        <div className="overflow-auto rounded-xl border border-gray-800 bg-gray-900 p-5">
          <JsonTree data={models} />
        </div>
      )}
    </div>
  );
}
