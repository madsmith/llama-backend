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
  stream: true,
  slot_prompt_similarity: null,
  repeat_penalty: null,
  repeat_last_n: null,
  slot_save_path: "",
  swa_full: false,
  extra_args: [],
};

const CTX_MIN = 1024;
const CTX_MAX = 200_000;

export default function ConfigEditor() {
  const [config, setConfig] = useState<ServerConfig>(defaultConfig);
  const [advanced, setAdvanced] = useState(false);
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

      <button
        type="button"
        onClick={() => setAdvanced(!advanced)}
        className="flex items-center gap-1.5 text-sm font-medium text-gray-400 hover:text-gray-200 transition"
      >
        <span className={`inline-block transition-transform ${advanced ? "rotate-90" : ""}`}>&#9654;</span>
        Advanced
      </button>

      {advanced && (
        <div className="space-y-4 border-l-2 border-gray-800 pl-6">
          <label className="flex items-center justify-between cursor-pointer">
            <span className="text-sm font-medium text-gray-400">
              Stream responses
            </span>
            <input
              type="checkbox"
              checked={config.stream}
              onChange={(e) => setConfig({ ...config, stream: e.target.checked })}
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 accent-blue-500"
            />
          </label>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Slot Prompt Similarity
              <span className="ml-1 text-xs text-gray-600">(-sps{config.slot_prompt_similarity == null ? ", disabled" : ""})</span>
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={100}
                value={config.slot_prompt_similarity == null ? 0 : config.slot_prompt_similarity * 100}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setConfig({ ...config, slot_prompt_similarity: v === 0 ? null : v / 100 });
                }}
                className={`flex-1 ${config.slot_prompt_similarity == null ? "opacity-30" : "accent-blue-500"}`}
              />
              <input
                type="number"
                step="0.01"
                min={0}
                max={1}
                value={config.slot_prompt_similarity ?? ""}
                placeholder="off"
                onChange={(e) =>
                  setConfig({ ...config, slot_prompt_similarity: e.target.value === "" ? null : Number(e.target.value) })
                }
                className="w-24 rounded-md border border-gray-700 bg-gray-800 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Repeat Penalty
              <span className="ml-1 text-xs text-gray-600">(1.0 = disabled{config.repeat_penalty == null ? ", off" : ""})</span>
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={100}
                value={config.repeat_penalty == null ? 0 : Math.round((config.repeat_penalty - 1.0) * 100)}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setConfig({ ...config, repeat_penalty: v === 0 ? null : 1.0 + v / 100 });
                }}
                className={`flex-1 ${config.repeat_penalty == null ? "opacity-30" : "accent-blue-500"}`}
              />
              <input
                type="number"
                step="0.05"
                min={1.0}
                max={2.0}
                value={config.repeat_penalty ?? ""}
                placeholder="off"
                onChange={(e) =>
                  setConfig({ ...config, repeat_penalty: e.target.value === "" ? null : Number(e.target.value) })
                }
                className="w-24 rounded-md border border-gray-700 bg-gray-800 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Repeat Last N
              <span className="ml-1 text-xs text-gray-600">(-1 = ctx_size{config.repeat_last_n == null ? ", off" : ""})</span>
            </label>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={-1}
                max={4096}
                value={config.repeat_last_n ?? -1}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setConfig({ ...config, repeat_last_n: v === -1 ? null : v });
                }}
                className={`flex-1 ${config.repeat_last_n == null ? "opacity-30" : "accent-blue-500"}`}
              />
              <input
                type="number"
                step="1"
                min={-1}
                max={4096}
                value={config.repeat_last_n ?? ""}
                placeholder="off"
                onChange={(e) =>
                  setConfig({ ...config, repeat_last_n: e.target.value === "" ? null : Number(e.target.value) })
                }
                className="w-24 rounded-md border border-gray-700 bg-gray-800 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Slot Save Path
              <span className="ml-1 text-xs text-gray-600">(prompt cache persistence, blank = disabled)</span>
            </label>
            <input
              type="text"
              value={config.slot_save_path}
              placeholder="/tmp/llama-slots"
              onChange={(e) => setConfig({ ...config, slot_save_path: e.target.value })}
              className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
            />
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={config.swa_full}
              onChange={(e) => setConfig({ ...config, swa_full: e.target.checked })}
              className="h-4 w-4 rounded border-gray-700 bg-gray-800 accent-blue-500"
            />
            <span className="text-sm font-medium text-gray-400">
              SWA Full Cache
              <span className="ml-1 text-xs text-gray-600">(for Gemma 2/3 when context exceeds window size)</span>
            </span>
          </label>

          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Extra Arguments
              <span className="ml-1 text-xs text-gray-600">(comma-separated)</span>
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
        </div>
      )}
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
