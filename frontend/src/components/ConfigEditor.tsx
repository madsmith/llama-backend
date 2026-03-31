import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { ServerConfig, ModelConfig, ModelAdvanced, RemoteManagerConfig } from "../api/types";
import type { SettingsTab } from "./config-defaults";
import { ToggleField, IntegerField, SliderField, TextField } from "./inputs";

const CTX_MIN = 1;
const CTX_MAX = 1_000_000;

interface Props {
  tab: SettingsTab;
  config: ServerConfig;
  setConfig: (c: ServerConfig) => void;
  modelIndex: number;
  remoteIndex: number;
  onDeleteModel: (index: number) => void;
  onDeleteRemote: (index: number) => void;
}

export default function ConfigEditor({
  tab,
  config,
  setConfig,
  modelIndex,
  remoteIndex,
  onDeleteModel,
  onDeleteRemote,
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


  const totalCtx = model.ctx_size * model.parallel;

  if (tab === "manager") {
    return (
      <div className="space-y-4 max-w-xl">
        <IntegerField
          label="Log Buffer Size"
          value={config.web_ui.log_buffer_size}
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, log_buffer_size: v ?? config.web_ui.log_buffer_size } })}
          min={1}
          bg="gray-800"
        />
        <p className="text-xs text-gray-600">
          Changes to log buffer size take effect on next restart.
        </p>
        <TextField
          label="Slot Save Path"
          value={config.web_ui.slot_save_path ?? ""}
          placeholder="./slot_saves"
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, slot_save_path: v } })}
          note="Base directory for KV cache saves. Each model gets a subdirectory by model ID."
          bg="gray-800"
        />
        <div className="border-t border-gray-700 pt-4 space-y-3">
          <span className="text-sm font-medium text-gray-300">Manager Uplink</span>
          <p className="text-xs text-gray-600">
            Allow other Llama Manager instances to connect to this one and proxy its models.
          </p>
          <ToggleField
            label="Enable Uplink"
            checked={config.manager_uplink?.enabled ?? false}
            onChange={(v) =>
              setConfig({
                ...config,
                manager_uplink: {
                  enabled: v,
                  token: config.manager_uplink?.token ?? "",
                },
              })
            }
            bg="gray-800"
          />
          {config.manager_uplink?.enabled && (
            <div>
              <TextField
                label="Authorization Token"
                value={config.manager_uplink?.token ?? ""}
                placeholder="Auto-generated on save"
                onChange={(v) => setConfig({ ...config, manager_uplink: { enabled: config.manager_uplink?.enabled ?? true, token: v } })}
                note="Connecting managers must provide this token. Clearing and saving generates a new one."
                mono
                bg="gray-800"
                actions={
                  <button
                    type="button"
                    title="Regenerate token"
                    onClick={() =>
                      api.generateUplinkToken().then(({ token }) =>
                        setConfig({ ...config, manager_uplink: { enabled: config.manager_uplink?.enabled ?? true, token } }),
                      )
                    }
                    className="rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-400 hover:text-gray-200 transition"
                  >
                    Regenerate
                  </button>
                }
              />
            </div>
          )}
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
                  <IntegerField
                    key={key}
                    label={label}
                    description={hint}
                    value={config.web_ui[key] ?? null}
                    onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, [key]: v } })}
                    nullable
                    min={500}
                    placeholder={placeholder}
                    bg="gray-800"
                  />
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
          The proxy server exposes OpenAI-compatible endpoints. External
          clients connect here instead of directly to llama-server.
        </p>
        <TextField
          label="Path to llama-server executable"
          value={config.api_server.llama_server_path}
          onChange={(v) => setConfig({ ...config, api_server: { ...config.api_server, llama_server_path: v } })}
          bg="gray-800"
        />
        <div className="grid grid-cols-2 gap-4">
          <TextField
            label="Proxy Host"
            value={config.api_server.host}
            onChange={(v) => setConfig({ ...config, api_server: { ...config.api_server, host: v } })}
            bg="gray-800"
          />
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
                  api_server: { ...config.api_server, port: Number(e.target.value) },
                })
              }
              className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none"
            />
          </div>
        </div>
        <IntegerField
          label="llama-server Starting Port"
          value={config.api_server.llama_server_starting_port}
          onChange={(v) => setConfig({ ...config, api_server: { ...config.api_server, llama_server_starting_port: v ?? config.api_server.llama_server_starting_port } })}
          min={1}
          max={65535}
          note="Each model gets assigned 127.0.0.1 on this port + its index in the models list."
          bg="gray-800"
        />
        <ToggleField
          label="JIT Model Server Start"
          description="Automatically start the model server when the proxy receives a request."
          checked={config.api_server.jit_model_server}
          onChange={(v) =>
            setConfig({
              ...config,
              api_server: {
                ...config.api_server,
                jit_model_server: v,
              },
            })
          }
          bg="gray-800"
        />
        {config.api_server.jit_model_server && (
          <IntegerField
            label="JIT Timeout"
            unit="seconds"
            value={config.api_server.jit_timeout ?? null}
            onChange={(v) => setConfig({ ...config, api_server: { ...config.api_server, jit_timeout: v } })}
            nullable
            min={10}
            max={600}
            placeholder="80"
            note="Maximum seconds to wait for the model server to become ready."
            bg="gray-800"
          />
        )}
        <p className="text-xs text-gray-600">
          Supported endpoints: <code>/v1/chat/completions</code>,{" "}
          <code>/v1/models</code>. Changes take effect on next application
          restart.
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

  if (tab.startsWith("remote-")) {
    const rm: RemoteManagerConfig = (config.remote_managers ?? [])[remoteIndex] ?? {
      name: null,
      host: "",
      port: 8000,
      token: "",
      reconnect_interval: 5,
      enabled: true,
    };

    const updateRemote = (patch: Partial<RemoteManagerConfig>) => {
      const remotes = [...(config.remote_managers ?? [])];
      remotes[remoteIndex] = { ...rm, ...patch };
      setConfig({ ...config, remote_managers: remotes });
    };

    return (
      <div className="space-y-4 max-w-xl">
        <p className="text-sm text-gray-400">
          Connect to another Llama Manager instance and proxy its models into this UI.
        </p>
        <TextField
          label="Name"
          value={rm.name ?? ""}
          placeholder={`Remote ${remoteIndex + 1}`}
          onChange={(v) => updateRemote({ name: v || null })}
          bg="gray-800"
        />
        <div className="flex gap-3">
          <div className="flex-1">
            <TextField
              label="Host"
              value={rm.host}
              placeholder="192.168.1.10"
              onChange={(v) => updateRemote({ host: v })}
              bg="gray-800"
            />
          </div>
          <div className="w-24">
            <IntegerField
              label="Port"
              value={rm.port}
              onChange={(v) => updateRemote({ port: v ?? rm.port })}
              min={1}
              max={65535}
              placeholder="8000"
              bg="gray-800"
            />
          </div>
        </div>
        <TextField
          label="Authorization Token"
          value={rm.token}
          placeholder="Token from remote manager's General settings"
          onChange={(v) => updateRemote({ token: v })}
          note="Must match the uplink token configured on the remote manager."
          mono
          bg="gray-800"
        />
        <IntegerField
          label="Reconnect Interval"
          unit="seconds"
          description="Seconds between reconnect attempts on disconnect"
          value={rm.reconnect_interval}
          onChange={(v) => updateRemote({ reconnect_interval: v ?? rm.reconnect_interval })}
          min={1}
          max={300}
          bg="gray-800"
        />
        <ToggleField
          label="Enabled"
          checked={rm.enabled}
          onChange={(v) => updateRemote({ enabled: v })}
          bg="gray-800"
        />
        <div className="flex items-center gap-3">
          <button
            onClick={save}
            disabled={saving}
            className="rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-40 transition"
          >
            {saving ? "Saving..." : "Save Configuration"}
          </button>
          {msg && <span className="text-sm text-gray-400">{msg}</span>}
          <button
            onClick={() => onDeleteRemote(remoteIndex)}
            className="ml-auto rounded-md bg-red-900/50 px-4 py-2 text-sm font-medium text-red-400 hover:bg-red-900 transition"
          >
            Delete Remote
          </button>
        </div>
      </div>
    );
  }

  const isRemote = (model.type ?? "local") === "remote";

  const NESTED_BG = "bg-gray-800";

  return (
    <div className="space-y-4 max-w-xl">
      <TextField
        label="Name"
        value={model.name ?? ""}
        placeholder={`Llama Server ${modelIndex + 1}`}
        onChange={(v) => updateModel({ name: v || null })}
        bg="gray-800"
      />
      <TextField
        label="ID"
        value={model.id ?? ""}
        placeholder={
          model.model_path
            ? model.model_path.split("/").pop()!.replace(/\.[^.]+$/, "").toLowerCase()
            : ""
        }
        onChange={(v) => updateModel({ id: v || null })}
        bg="gray-800"
      />

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
              <TextField
                label="Remote Address"
                value={model.remote_address ?? ""}
                placeholder="http://192.168.1.100:8080"
                onChange={(v) => updateModel({ remote_address: v })}
              />
              <TextField
                label="Remote Model ID"
                sublabel="(optional, rewrites model field in forwarded requests)"
                value={model.remote_model_id ?? ""}
                placeholder="model-id-on-remote"
                onChange={(v) => updateModel({ remote_model_id: v || null })}
              />
            </>
          ) : (
            <>
              <TextField
                label="Model Path"
                value={model.model_path}
                onChange={(v) => updateModel({ model_path: v })}
              />

              <SliderField
                label="Parallel Slots"
                value={model.parallel}
                onChange={(v) => updateModel({ parallel: v! })}
                sliderMin={1}
                sliderMax={8}
              />

              <SliderField
                label="Context Size per Slot"
                value={model.ctx_size}
                onChange={(v) => v !== null && setCtxSize(v)}
                sliderMin={CTX_MIN}
                sliderMax={CTX_MAX}
                snap={1024}
                note={`Total context: ${totalCtx.toLocaleString()} (${model.ctx_size.toLocaleString()} × ${model.parallel} slots)`}
              />

              <IntegerField
                label="GPU Layers"
                value={model.n_gpu_layers === -1 ? null : model.n_gpu_layers}
                onChange={(v) => updateModel({ n_gpu_layers: v ?? -1 })}
                min={0}
                placeholder="-1"
                nullable
              />

              <ToggleField
                label="Auto Start"
                checked={model.auto_start}
                onChange={(v) => updateModel({ auto_start: v })}
              />

              <ToggleField
                label="Allow Proxy"
                checked={model.allow_proxy ?? true}
                onChange={(v) => updateModel({ allow_proxy: v })}
              />

              <IntegerField
                label="Model TTL"
                unit="minutes"
                value={model.model_ttl}
                onChange={(v) => updateModel({ model_ttl: v })}
                nullable
                min={1}
                placeholder="indefinite"
              />

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

                  {/* ── Output ── */}
                  <ToggleField
                    label="Stream Responses"
                    tip="Send tokens as they are generated rather than waiting for the full reply"
                    checked={adv.stream}
                    onChange={(v) => updateAdv({ stream: v })}
                  />

                  <ToggleField
                    label="Supports Developer Role"
                    tip={<>Accept the <code>developer</code> system role used by Claude clients and treat it as <code>system</code></>}
                    checked={adv.supports_developer_role}
                    onChange={(v) => updateAdv({ supports_developer_role: v })}
                  />

                  <ToggleField
                    label="Use Chat Template"
                    tip={<>Apply the model's built-in Jinja2 chat template. Recommended for most models.<div className="mt-1 font-mono text-gray-400">--jinja</div></>}
                    checked={adv.use_jinja ?? true}
                    onChange={(v) => updateAdv({ use_jinja: v })}
                  />

                  <IntegerField
                    label="Max Prediction Tokens"
                    tip={<>Cap the number of tokens generated per request. Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--n-predict</div></>}
                    value={adv.max_prediction_tokens}
                    onChange={(v) => updateAdv({ max_prediction_tokens: v })}
                    nullable
                    min={1}
                    placeholder="unlimited"
                  />

                  <TextField
                    label="Stop Token"
                    tip={<>Halt generation when this string appears in the output.<div className="mt-1 font-mono text-gray-400">--stop</div></>}
                    value={adv.stop ?? ""}
                    placeholder="e.g. </s>"
                    onChange={(v) => updateAdv({ stop: v || null })}
                    mono
                  />

                  {/* ── Sampling ── */}
                  <SliderField
                    label="Temperature"
                    tip={<>Randomness of token selection. Lower = more focused, higher = more creative. Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--temp</div><div className="mt-1 text-gray-500">Range: 0.0–1.5 · Default: 0.8</div></>}
                    value={adv.temperature}
                    onChange={(v) => updateAdv({ temperature: v })}
                    sliderMin={0}
                    sliderMax={150}
                    scale={100}
                    precision={2}
                    placeholder="0.8"
                    nullSliderPosition={80}
                  />

                  <SliderField
                    label="Nucleus Sampling (Top-P)"
                    tip={<>Restrict selection to the smallest token set whose cumulative probability exceeds P. Lower values produce more focused output. Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--top-p</div><div className="mt-1 text-gray-500">Recommended: 0.8–0.99 · Default: 0.95</div></>}
                    value={adv.top_p}
                    onChange={(v) => updateAdv({ top_p: v })}
                    sliderMin={5}
                    sliderMax={100}
                    scale={100}
                    precision={2}
                    placeholder="0.95"
                    nullSliderPosition={95}
                  />

                  <SliderField
                    label="Top-K Sampling"
                    tip={<>Limit selection to the K highest-probability tokens. 0 disables the filter. Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--top-k</div><div className="mt-1 text-gray-500">Range: 0–200 · Default: 40</div></>}
                    value={adv.top_k}
                    onChange={(v) => updateAdv({ top_k: v })}
                    sliderMin={0}
                    sliderMax={200}
                    placeholder="40"
                    nullSliderPosition={40}
                  />

                  <SliderField
                    label="Min-P Filter"
                    tip={<>Remove tokens whose probability falls below min_p × P(top token). Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--min-p</div><div className="mt-1 text-gray-500">Recommended: 0.0–0.05 · Default: 0.05</div></>}
                    value={adv.min_p}
                    onChange={(v) => updateAdv({ min_p: v })}
                    sliderMin={0}
                    sliderMax={200}
                    scale={1000}
                    precision={3}
                    placeholder="0.05"
                    nullSliderPosition={50}
                  />

                  <SliderField
                    label="Repeat Penalty"
                    tip={<>Penalise tokens that have already appeared to reduce repetition. 1.0 disables. Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--repeat-penalty</div><div className="mt-1 text-gray-500">Range: 1.0–2.0 · 1.0 = disabled</div></>}
                    value={adv.repeat_penalty}
                    onChange={(v) => updateAdv({ repeat_penalty: v })}
                    sliderMin={0}
                    sliderMax={100}
                    scale={100}
                    offset={1.0}
                    precision={2}
                    placeholder="1.0"
                    nullSliderPosition={0}
                    nullAtSliderMin
                  />

                  <SliderField
                    label="Repeat Last N"
                    tip={<>Number of recent tokens considered when applying repeat penalty. Clear to use the server default.<div className="mt-1 font-mono text-gray-400">--repeat-last-n</div><div className="mt-1 text-gray-500">Range: 0–4096 · −1 = ctx size · Default: 64</div></>}
                    value={adv.repeat_last_n}
                    onChange={(v) => updateAdv({ repeat_last_n: v })}
                    sliderMin={-1}
                    sliderMax={4096}
                    placeholder="64"
                    nullSliderPosition={64}
                  />

                  {/* ── Slot / KV cache ── */}
                  <SliderField
                    label="Slot Prompt Similarity"
                    tip={<>Reuse a cached slot whose stored prompt matches at least this fraction of the incoming prompt. Clear to disable slot reuse.<div className="mt-1 font-mono text-gray-400">--slot-prompt-similarity</div><div className="mt-1 text-gray-500">Range: 0.0–1.0</div></>}
                    value={adv.slot_prompt_similarity}
                    onChange={(v) => updateAdv({ slot_prompt_similarity: v })}
                    sliderMin={0}
                    sliderMax={100}
                    scale={100}
                    precision={2}
                    placeholder="off"
                    nullAtSliderMin
                  />

                  <ToggleField
                    label="Auto-Fit Memory"
                    tip={<>Automatically reduce unset parameters (e.g. context size) to fit in available device memory. Disable when using KV cache slot save/restore — if the model starts with different parameters than when the slot was saved, the slot file will be incompatible.<div className="mt-1 font-mono text-gray-400">--fit on|off</div><div className="mt-1 text-gray-500">Default: on</div></>}
                    checked={adv.fit ?? true}
                    onChange={(v) => updateAdv({ fit: v })}
                  />

                  <ToggleField
                    label="KV Cache"
                    tip={<>Persist and restore slot KV state between requests. Experimental.<div className="mt-1 font-mono text-gray-400">--slots</div></>}
                    checked={adv.kv_cache}
                    onChange={(v) => updateAdv({ kv_cache: v })}
                  />

                  <TextField
                    label="Slot Save Path"
                    tip={<>Directory for KV cache slot files. Blank uses the global path from the Manager tab.<div className="mt-1 font-mono text-gray-400">--slot-save-path</div></>}
                    value={adv.slot_save_path}
                    placeholder="/tmp/llama-slots"
                    onChange={(v) => updateAdv({ slot_save_path: v })}
                  />

                  <ToggleField
                    label="SWA Full Cache"
                    tip={<>Use a full-size KV cache instead of sliding window attention. Required for Gemma 2/3 when context exceeds the model's window size.<div className="mt-1 font-mono text-gray-400">--swa-full</div></>}
                    checked={adv.swa_full}
                    onChange={(v) => updateAdv({ swa_full: v })}
                  />

                  {/* ── Binary / misc ── */}
                  <TextField
                    label="llama-server Path Override"
                    tip="Use a different llama-server binary for this model only. Blank inherits the path from the Proxy Server tab."
                    value={adv.llama_server_path}
                    placeholder={config.api_server.llama_server_path || "not set"}
                    onChange={(v) => updateAdv({ llama_server_path: v })}
                  />

                  <TextField
                    label="Extra Arguments"
                    tip="Additional flags passed directly to llama-server, comma-separated."
                    value={extraArgsRaw}
                    onChange={setExtraArgsRaw}
                    onBlur={(v) =>
                      updateAdv({
                        extra_args: v.split(",").map((s) => s.trim()).filter(Boolean),
                      })
                    }
                  />
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
