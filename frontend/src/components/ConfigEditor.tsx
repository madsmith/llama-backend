import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { ServerConfig, ModelConfig, ModelAdvanced } from "../api/types";
import type { SettingsTab } from "./config-defaults";

const CTX_MIN = 1024;
const CTX_MAX = 200_000;

interface Props {
  tab: SettingsTab;
  config: ServerConfig;
  setConfig: (c: ServerConfig) => void;
  modelIndex: number;
  onDeleteModel: (index: number) => void;
}

export default function ConfigEditor({
  tab,
  config,
  setConfig,
  modelIndex,
  onDeleteModel,
}: Props) {
  const [advanced, setAdvanced] = useState(false);
  const [managerAdvanced, setManagerAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const model = config.models[modelIndex];
  const adv = model.advanced;

  const extraArgsJoined = adv.extra_args.join(", ");
  const [extraArgsRaw, setExtraArgsRaw] = useState(extraArgsJoined);

  useEffect(() => {
    setExtraArgsRaw(extraArgsJoined);
  }, [modelIndex, extraArgsJoined]);

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

  const updateModel = (patch: Partial<ModelConfig>) => {
    setConfig({
      ...config,
      models: config.models.map((m, i) =>
        i === modelIndex ? { ...m, ...patch } : m,
      ),
    });
  };

  const updateAdv = (patch: Partial<ModelAdvanced>) => {
    updateModel({ advanced: { ...adv, ...patch } });
  };

  const setCtxSize = (v: number, snap = false) => {
    let clamped = Math.max(CTX_MIN, Math.min(CTX_MAX, v));
    if (snap) clamped = Math.round(clamped / 1024) * 1024;
    updateModel({ ctx_size: clamped });
  };

  const modelField = (
    label: string,
    key: keyof ModelConfig,
    type: "text" | "number" = "text",
  ) => (
    <div>
      <label className="block text-sm font-medium text-gray-400 mb-1">
        {label}
      </label>
      <input
        type={type}
        value={model[key] as string | number}
        onChange={(e) =>
          updateModel({
            [key]: type === "number" ? Number(e.target.value) : e.target.value,
          })
        }
        className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
      />
    </div>
  );

  const totalCtx = model.ctx_size * model.parallel;

  if (tab === "manager") {
    return (
      <div className="space-y-4 max-w-xl">
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            Log Buffer Size
          </label>
          <input
            type="number"
            value={config.web_ui.log_buffer_size}
            onChange={(e) =>
              setConfig({
                ...config,
                web_ui: {
                  ...config.web_ui,
                  log_buffer_size: Number(e.target.value),
                },
              })
            }
            className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
          />
        </div>
        <p className="text-xs text-gray-600">
          Changes to log buffer size take effect on next restart.
        </p>
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            Slot Save Path
          </label>
          <input
            type="text"
            value={config.web_ui.slot_save_path ?? ""}
            placeholder="./slot_saves"
            onChange={(e) =>
              setConfig({
                ...config,
                web_ui: {
                  ...config.web_ui,
                  slot_save_path: e.target.value,
                },
              })
            }
            className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-gray-600">
            Base directory for KV cache saves. Each model gets a subdirectory by
            model ID.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setManagerAdvanced(!managerAdvanced)}
          className="flex items-center gap-1.5 text-sm font-medium text-gray-400 hover:text-gray-200 transition"
        >
          <span
            className={`inline-block transition-transform ${managerAdvanced ? "rotate-90" : ""}`}
          >
            &#9654;
          </span>
          Advanced
        </button>
        {managerAdvanced && (
          <div className="space-y-3 border-l-2 border-gray-700 pl-6">
            <div>
              <span className="text-sm font-medium text-gray-400">
                Polling Rates
              </span>
              <div className="mt-2 ml-4 space-y-3">
                {(
                  [
                    [
                      "Server Status",
                      "3000",
                      "poll_server_status",
                      "Process state and uptime",
                    ],
                    [
                      "Proxy Status",
                      "5000",
                      "poll_proxy_status",
                      "Proxy server availability",
                    ],
                    ["Health", "5000", "poll_health", "Endpoint health checks"],
                    ["Slots", "5000", "poll_slots", "Slot utilization data"],
                    [
                      "Slots (active)",
                      "500",
                      "poll_slots_active",
                      "Rate when a slot is processing",
                    ],
                  ] as const
                ).map(([label, placeholder, key, hint]) => (
                  <div key={key} className="flex items-center justify-between">
                    <div>
                      <span className="text-sm text-gray-400">{label}</span>
                      <p className="text-xs text-gray-600">{hint}</p>
                    </div>
                    <input
                      type="number"
                      min={500}
                      value={config.web_ui[key] ?? ""}
                      placeholder={placeholder}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          web_ui: {
                            ...config.web_ui,
                            [key]:
                              e.target.value === ""
                                ? null
                                : Number(e.target.value),
                          },
                        })
                      }
                      className="w-28 rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 text-right focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                ))}
              </div>
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

  if (tab === "proxy") {
    return (
      <div className="space-y-4 max-w-xl">
        <p className="text-sm text-gray-400">
          The proxy server exposes OpenAI-compatible and Anthropic Messages API
          endpoints. External clients connect here instead of directly to
          llama-server.
        </p>
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            Path to llama-server executable
          </label>
          <input
            type="text"
            value={config.api_server.llama_server_path}
            onChange={(e) =>
              setConfig({
                ...config,
                api_server: {
                  ...config.api_server,
                  llama_server_path: e.target.value,
                },
              })
            }
            className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Proxy Host
            </label>
            <input
              type="text"
              value={config.api_server.host}
              onChange={(e) =>
                setConfig({
                  ...config,
                  api_server: {
                    ...config.api_server,
                    host: e.target.value,
                  },
                })
              }
              className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Proxy Port
            </label>
            <input
              type="number"
              value={config.api_server.port}
              onChange={(e) =>
                setConfig({
                  ...config,
                  api_server: {
                    ...config.api_server,
                    port: Number(e.target.value),
                  },
                })
              }
              className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
            />
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            llama-server Starting Port
          </label>
          <input
            type="number"
            value={config.api_server.llama_server_starting_port}
            onChange={(e) =>
              setConfig({
                ...config,
                api_server: {
                  ...config.api_server,
                  llama_server_starting_port: Number(e.target.value),
                },
              })
            }
            className="w-28 rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-gray-600">
            Each model gets assigned 127.0.0.1 on this port + its index in the
            models list.
          </p>
        </div>
        <label className="flex items-center justify-between cursor-pointer">
          <div>
            <span className="text-sm font-medium text-gray-400">
              JIT Model Server Start
            </span>
            <p className="text-xs text-gray-600">
              Automatically start the model server when the proxy receives a
              request.
            </p>
          </div>
          <input
            type="checkbox"
            checked={config.api_server.jit_model_server}
            onChange={(e) =>
              setConfig({
                ...config,
                api_server: {
                  ...config.api_server,
                  jit_model_server: e.target.checked,
                },
              })
            }
            className="h-4 w-4 rounded border-gray-700 bg-gray-800 accent-blue-500"
          />
        </label>
        {config.api_server.jit_model_server && (
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              JIT Timeout (seconds)
            </label>
            <input
              type="number"
              min={10}
              max={600}
              value={config.api_server.jit_timeout ?? ""}
              placeholder="80"
              onChange={(e) =>
                setConfig({
                  ...config,
                  api_server: {
                    ...config.api_server,
                    jit_timeout:
                      e.target.value === "" ? null : Number(e.target.value),
                  },
                })
              }
              className="w-28 rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-gray-600">
              Maximum seconds to wait for the model server to become ready.
              Default: 80.
            </p>
          </div>
        )}
        <p className="text-xs text-gray-600">
          Supported endpoints: <code>/v1/chat/completions</code>,{" "}
          <code>/v1/models</code>, <code>/v1/messages</code> (Anthropic format).
          Changes take effect on next application restart.
        </p>
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

  const isRemote = (model.type ?? "local") === "remote";

  const NESTED_BG = "bg-gray-800";

  return (
    <div className="space-y-4 max-w-xl">
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          Name
        </label>
        <input
          type="text"
          value={model.name ?? ""}
          placeholder={`Llama Server ${modelIndex + 1}`}
          onChange={(e) => updateModel({ name: e.target.value || null })}
          className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-400 mb-1">
          ID
        </label>
        <input
          type="text"
          value={model.id ?? ""}
          placeholder={
            model.model_path
              ? model.model_path
                  .split("/")
                  .pop()!
                  .replace(/\.[^.]+$/, "")
                  .toLowerCase()
              : ""
          }
          onChange={(e) => updateModel({ id: e.target.value || null })}
          className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
        />
      </div>

      {/* Local/Remote tabs + nested panel */}
      <div>
        <div className="flex items-end gap-1">
          <button
            type="button"
            onClick={() => updateModel({ type: "local" })}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition ${
              !isRemote
                ? `${NESTED_BG} text-gray-100`
                : "bg-gray-800/40 text-gray-500 hover:text-gray-300"
            }`}
          >
            Local
          </button>
          <button
            type="button"
            onClick={() => updateModel({ type: "remote" })}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition ${
              isRemote
                ? `${NESTED_BG} text-gray-100`
                : "bg-gray-800/40 text-gray-500 hover:text-gray-300"
            }`}
          >
            Remote
          </button>
        </div>
        <div
          className={`${NESTED_BG} rounded-b-lg rounded-tr-lg p-5 space-y-4`}
        >
          {isRemote ? (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Remote Address
                </label>
                <input
                  type="text"
                  value={model.remote_address ?? ""}
                  placeholder="http://192.168.1.100:8080"
                  onChange={(e) =>
                    updateModel({ remote_address: e.target.value })
                  }
                  className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Remote Model ID
                  <span className="ml-1 text-xs text-gray-600">
                    (optional, rewrites model field in forwarded requests)
                  </span>
                </label>
                <input
                  type="text"
                  value={model.remote_model_id ?? ""}
                  placeholder="model-id-on-remote"
                  onChange={(e) =>
                    updateModel({ remote_model_id: e.target.value || null })
                  }
                  className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
                />
              </div>
            </>
          ) : (
            <>
              {modelField("Model Path", "model_path")}

              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1">
                  Context Size per Slot
                </label>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={CTX_MIN}
                    max={CTX_MAX}
                    value={model.ctx_size}
                    onChange={(e) => setCtxSize(Number(e.target.value), true)}
                    className="flex-1 accent-blue-500"
                  />
                  <input
                    type="number"
                    min={1}
                    max={CTX_MAX}
                    value={model.ctx_size}
                    onChange={(e) => setCtxSize(Number(e.target.value))}
                    className="w-24 rounded-md border border-gray-700 bg-gray-900 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
                  />
                </div>
                <div className="mt-1 text-xs text-gray-500">
                  Total context: {totalCtx.toLocaleString()} (
                  {model.ctx_size.toLocaleString()} &times; {model.parallel}{" "}
                  slots)
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
                    value={model.parallel}
                    onChange={(e) =>
                      updateModel({ parallel: Number(e.target.value) })
                    }
                    className="flex-1 accent-blue-500"
                  />
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={model.parallel}
                    onChange={(e) =>
                      updateModel({
                        parallel: Math.max(
                          1,
                          Math.min(8, Number(e.target.value)),
                        ),
                      })
                    }
                    className="w-24 rounded-md border border-gray-700 bg-gray-900 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
                  />
                </div>
              </div>
              {modelField("GPU Layers", "n_gpu_layers", "number")}

              <label className="flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-sm font-medium text-gray-400">
                    Auto Start
                  </span>
                </div>
                <input
                  type="checkbox"
                  checked={model.auto_start}
                  onChange={(e) =>
                    updateModel({ auto_start: e.target.checked })
                  }
                  className="h-4 w-4 rounded border-gray-700 bg-gray-900 accent-blue-500"
                />
              </label>

              <label className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-400">
                  Model TTL
                </span>
                <input
                  type="number"
                  min={1}
                  value={model.model_ttl ?? ""}
                  placeholder="indefinite"
                  onChange={(e) =>
                    updateModel({
                      model_ttl:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                  className="w-28 rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 text-right focus:border-blue-500 focus:outline-none"
                />
              </label>

              <button
                type="button"
                onClick={() => setAdvanced(!advanced)}
                className="flex items-center gap-1.5 text-sm font-medium text-gray-400 hover:text-gray-200 transition"
              >
                <span
                  className={`inline-block transition-transform ${advanced ? "rotate-90" : ""}`}
                >
                  &#9654;
                </span>
                Advanced
              </button>

              {advanced && (
                <div className="space-y-4 border-l-2 border-gray-700 pl-6">
                  <label className="flex items-center justify-between cursor-pointer">
                    <span className="text-sm font-medium text-gray-400">
                      Stream responses
                    </span>
                    <input
                      type="checkbox"
                      checked={adv.stream}
                      onChange={(e) => updateAdv({ stream: e.target.checked })}
                      className="h-4 w-4 rounded border-gray-700 bg-gray-900 accent-blue-500"
                    />
                  </label>

                  <label className="flex items-center justify-between cursor-pointer">
                    <span className="text-sm font-medium text-gray-400">
                      Supports Developer Role
                    </span>
                    <input
                      type="checkbox"
                      checked={adv.supports_developer_role}
                      onChange={(e) =>
                        updateAdv({
                          supports_developer_role: e.target.checked,
                        })
                      }
                      className="h-4 w-4 rounded border-gray-700 bg-gray-900 accent-blue-500"
                    />
                  </label>

                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">
                      Slot Prompt Similarity
                      <span className="ml-1 text-xs text-gray-600">
                        (-sps
                        {adv.slot_prompt_similarity == null ? ", disabled" : ""}
                        )
                      </span>
                    </label>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={
                          adv.slot_prompt_similarity == null
                            ? 0
                            : adv.slot_prompt_similarity * 100
                        }
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          updateAdv({
                            slot_prompt_similarity: v === 0 ? null : v / 100,
                          });
                        }}
                        className={`flex-1 ${adv.slot_prompt_similarity == null ? "opacity-30" : "accent-blue-500"}`}
                      />
                      <input
                        type="number"
                        step="0.01"
                        min={0}
                        max={1}
                        value={adv.slot_prompt_similarity ?? ""}
                        placeholder="off"
                        onChange={(e) =>
                          updateAdv({
                            slot_prompt_similarity:
                              e.target.value === ""
                                ? null
                                : Number(e.target.value),
                          })
                        }
                        className="w-24 rounded-md border border-gray-700 bg-gray-900 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">
                      Repeat Penalty
                      <span className="ml-1 text-xs text-gray-600">
                        (1.0 = disabled
                        {adv.repeat_penalty == null ? ", off" : ""})
                      </span>
                    </label>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={
                          adv.repeat_penalty == null
                            ? 0
                            : Math.round((adv.repeat_penalty - 1.0) * 100)
                        }
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          updateAdv({
                            repeat_penalty: v === 0 ? null : 1.0 + v / 100,
                          });
                        }}
                        className={`flex-1 ${adv.repeat_penalty == null ? "opacity-30" : "accent-blue-500"}`}
                      />
                      <input
                        type="number"
                        step="0.05"
                        min={1.0}
                        max={2.0}
                        value={adv.repeat_penalty ?? ""}
                        placeholder="off"
                        onChange={(e) =>
                          updateAdv({
                            repeat_penalty:
                              e.target.value === ""
                                ? null
                                : Number(e.target.value),
                          })
                        }
                        className="w-24 rounded-md border border-gray-700 bg-gray-900 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">
                      Repeat Last N
                      <span className="ml-1 text-xs text-gray-600">
                        (-1 = ctx_size{adv.repeat_last_n == null ? ", off" : ""}
                        )
                      </span>
                    </label>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={-1}
                        max={4096}
                        value={adv.repeat_last_n ?? -1}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          updateAdv({ repeat_last_n: v === -1 ? null : v });
                        }}
                        className={`flex-1 ${adv.repeat_last_n == null ? "opacity-30" : "accent-blue-500"}`}
                      />
                      <input
                        type="number"
                        step="1"
                        min={-1}
                        max={4096}
                        value={adv.repeat_last_n ?? ""}
                        placeholder="off"
                        onChange={(e) =>
                          updateAdv({
                            repeat_last_n:
                              e.target.value === ""
                                ? null
                                : Number(e.target.value),
                          })
                        }
                        className="w-24 rounded-md border border-gray-700 bg-gray-900 pl-3 pr-1 py-2 text-sm text-gray-100 font-mono text-right focus:border-blue-500 focus:outline-none"
                      />
                    </div>
                  </div>

                  <label className="flex items-center justify-between cursor-pointer">
                    <div>
                      <span className="text-sm font-medium text-gray-400">
                        KV Cache (experimental)
                      </span>
                      <p className="text-xs text-gray-600">
                        Enable slot save/restore with --slots
                      </p>
                    </div>
                    <input
                      type="checkbox"
                      checked={adv.kv_cache}
                      onChange={(e) =>
                        updateAdv({ kv_cache: e.target.checked })
                      }
                      className="h-4 w-4 rounded border-gray-700 bg-gray-900 accent-blue-500"
                    />
                  </label>

                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">
                      Slot Save Path
                      <span className="ml-1 text-xs text-gray-600">
                        (override, blank = use global path)
                      </span>
                    </label>
                    <input
                      type="text"
                      value={adv.slot_save_path}
                      placeholder="/tmp/llama-slots"
                      onChange={(e) =>
                        updateAdv({ slot_save_path: e.target.value })
                      }
                      className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
                    />
                  </div>

                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={adv.swa_full}
                      onChange={(e) =>
                        updateAdv({ swa_full: e.target.checked })
                      }
                      className="h-4 w-4 rounded border-gray-700 bg-gray-900 accent-blue-500"
                    />
                    <span className="text-sm font-medium text-gray-400">
                      SWA Full Cache
                      <span className="ml-1 text-xs text-gray-600">
                        (for Gemma 2/3 when context exceeds window size)
                      </span>
                    </span>
                  </label>

                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">
                      llama-server Path Override
                      <span className="ml-1 text-xs text-gray-600">
                        (blank = use default from Proxy Server tab)
                      </span>
                    </label>
                    <input
                      type="text"
                      value={adv.llama_server_path}
                      placeholder={
                        config.api_server.llama_server_path || "not set"
                      }
                      onChange={(e) =>
                        updateAdv({ llama_server_path: e.target.value })
                      }
                      className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
                    />
                  </div>

                  <label className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-400">
                      Max Prediction Tokens
                      <span className="ml-1 text-xs text-gray-600">
                        (--n-predict
                        {adv.max_prediction_tokens == null ? ", off" : ""})
                      </span>
                    </span>
                    <input
                      type="number"
                      step="1"
                      min={1}
                      value={adv.max_prediction_tokens ?? ""}
                      placeholder="4096"
                      onChange={(e) =>
                        updateAdv({
                          max_prediction_tokens:
                            e.target.value === ""
                              ? null
                              : Number(e.target.value),
                        })
                      }
                      className="w-28 rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 text-right focus:border-blue-500 focus:outline-none"
                    />
                  </label>

                  <div>
                    <label className="block text-sm font-medium text-gray-400 mb-1">
                      Extra Arguments
                      <span className="ml-1 text-xs text-gray-600">
                        (comma-separated)
                      </span>
                    </label>
                    <input
                      type="text"
                      value={extraArgsRaw}
                      onChange={(e) => setExtraArgsRaw(e.target.value)}
                      onBlur={(e) =>
                        updateAdv({
                          extra_args: e.target.value
                            .split(",")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                      className="w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
                    />
                  </div>
                </div>
              )}
            </>
          )}
        </div>
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
        {modelIndex > 0 && (
          <button
            onClick={() => onDeleteModel(modelIndex)}
            className="ml-auto rounded-md bg-red-900/50 px-4 py-2 text-sm font-medium text-red-400 hover:bg-red-900 transition"
          >
            Delete Model
          </button>
        )}
      </div>
    </div>
  );
}
