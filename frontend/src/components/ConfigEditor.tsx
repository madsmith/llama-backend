import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ServerConfig } from "../api/types";

const defaultConfig: ServerConfig = {
  llama_server_path: "",
  model_path: "",
  host: "127.0.0.1",
  port: 8080,
  ctx_size: 65536,
  n_gpu_layers: -1,
  parallel: 2,
  extra_args: [],
};

const CTX_MIN = 1024;
const CTX_MAX = 200_000;

export default function ConfigEditor() {
  const [config, setConfig] = useState<ServerConfig>(defaultConfig);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      const saved = await api.putConfig(config);
      setConfig(saved);
      setMsg("Saved");
    } catch {
      setMsg("Error saving config");
    } finally {
      setSaving(false);
    }
  };

  const setCtxSize = (v: number, snap = false) => {
    let clamped = Math.max(CTX_MIN, Math.min(CTX_MAX, v));
    if (snap) clamped = Math.round(clamped / 1024) * 1024;
    setConfig({ ...config, ctx_size: clamped });
  };

  const field = (
    label: string,
    key: keyof ServerConfig,
    type: "text" | "number" = "text",
  ) => (
    <div>
      <label className="block text-sm font-medium text-gray-400 mb-1">
        {label}
      </label>
      <input
        type={type}
        value={config[key] as string | number}
        onChange={(e) =>
          setConfig({
            ...config,
            [key]: type === "number" ? Number(e.target.value) : e.target.value,
          })
        }
        className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
      />
    </div>
  );

  const totalCtx = config.ctx_size * config.parallel;

  return (
    <div className="space-y-4 max-w-xl">
      {field("llama-server Path", "llama_server_path")}
      {field("Model Path", "model_path")}
      <div className="grid grid-cols-2 gap-4">
        {field("Host", "host")}
        {field("Port", "port", "number")}
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          Context Size per Slot
        </label>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={CTX_MIN}
            max={CTX_MAX}
            value={config.ctx_size}
            onChange={(e) => setCtxSize(Number(e.target.value), true)}
            className="flex-1 accent-blue-500"
          />
          <input
            type="number"
            min={1}
            max={CTX_MAX}
            value={config.ctx_size}
            onChange={(e) => setCtxSize(Number(e.target.value))}
            className="w-24 rounded-md border border-gray-700 bg-gray-800 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div className="mt-1 text-xs text-gray-500">
          Total context: {totalCtx.toLocaleString()} ({config.ctx_size.toLocaleString()} &times; {config.parallel} slots)
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          Parallel Slots
        </label>
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={1}
            max={8}
            value={config.parallel}
            onChange={(e) =>
              setConfig({ ...config, parallel: Number(e.target.value) })
            }
            className="flex-1 accent-blue-500"
          />
          <input
            type="number"
            min={1}
            max={8}
            value={config.parallel}
            onChange={(e) =>
              setConfig({ ...config, parallel: Math.max(1, Math.min(8, Number(e.target.value))) })
            }
            className="w-24 rounded-md border border-gray-700 bg-gray-800 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
          />
        </div>
      </div>
      {field("GPU Layers", "n_gpu_layers", "number")}
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          Extra Arguments (comma-separated)
        </label>
        <input
          type="text"
          value={config.extra_args.join(", ")}
          onChange={(e) =>
            setConfig({
              ...config,
              extra_args: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-40 transition"
        >
          {saving ? "Saving..." : "Save Configuration"}
        </button>
        {msg && <span className="text-sm text-gray-400">{msg}</span>}
      </div>
    </div>
  );
}
