import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import {
  applyModelFile,
  draftProblem,
  formatBytes,
  kvCacheBytes,
  offloadableLayers,
  projectorProblem,
  useGgufInfo,
} from "../api/gguf";
import type { ServerConfig, ModelConfig, ModelAdvanced, RemoteManagerConfig } from "../api/types";
import { defaultAutoload, type SettingsTab } from "./config-defaults";
import { ToggleField, IntegerField, SliderField, TextField, PathField, TipFlag, SectionDivider } from "./inputs";

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
  const [totalRamMib, setTotalRamMib] = useState<number | null>(null);
  const [cwd, setCwd] = useState("");
  const [freeMemory, setFreeMemory] = useState<{ available_mib: number; total_mib: number; source: string } | null>(null);

  useEffect(() => {
    api.getSystemInfo().then((r) => { setTotalRamMib(r.total_ram_mib); setCwd(r.cwd ?? ""); setFreeMemory(r.memory ?? null); }).catch(() => {});
  }, []);
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

  const setCtxSize = (v: number | null, snap = false) => {
    if (v === null) {
      updateModel({ ctx_size: null });
      return;
    }
    let clamped = Math.max(CTX_MIN, Math.min(CTX_MAX, v));
    if (snap) clamped = Math.round(clamped / 1024) * 1024;
    updateModel({ ctx_size: clamped });
  };

  // GGUF metadata for the files this model points at (local models only).
  const isLocal = (model.type ?? "local") !== "remote";
  const modelFile = useGgufInfo(isLocal ? model.model_path : "", { projectors: true });
  const projFile = useGgufInfo(isLocal ? adv.mmproj_path ?? "" : "");
  const draftFile = useGgufInfo(isLocal ? adv.draft_model_path ?? "" : "");
  const modelInfo = modelFile.info;
  const trainedCtx = modelInfo?.context_length ?? null;
  const modelLayers = offloadableLayers(modelInfo);
  const draftLayers = offloadableLayers(draftFile.info);

  // When a model file is picked, adjust settings from it once its header loads.
  const pendingModelPath = useRef<string | null>(null);
  const [fileChanges, setFileChanges] = useState<{ changes: string[]; before: ModelConfig } | null>(null);
  useEffect(() => {
    pendingModelPath.current = null;
    setFileChanges(null);
  }, [modelIndex]);
  useEffect(() => {
    if (!modelInfo || pendingModelPath.current !== modelFile.path) return;
    pendingModelPath.current = null;
    const { model: next, changes } = applyModelFile(model, modelInfo);
    if (changes.length) {
      updateModel(next);
      setFileChanges({ changes, before: model });
    }
  }, [modelInfo]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickModelPath = (v: string) => {
    pendingModelPath.current = v;
    setFileChanges(null);
    updateModel({ model_path: v });
  };

  const kvUnified = model.kv_unified ?? false;
  const perSlotCtx = model.ctx_size ?? trainedCtx;
  const totalCtx = perSlotCtx == null ? null : kvUnified ? perSlotCtx : perSlotCtx * model.parallel;
  const kvBytes = perSlotCtx == null ? null : kvCacheBytes(modelInfo, perSlotCtx, model.parallel, kvUnified, adv.swa_full);
  const ramBytes = totalRamMib != null ? totalRamMib * 1024 * 1024 : null;

  if (tab === "manager") {
    const autoload = { ...defaultAutoload, ...config.autoload };
    const setAutoload = (patch: Partial<typeof autoload>) =>
      setConfig({ ...config, autoload: { ...autoload, ...patch } });
    const autoFolders = autoload.folders.length ? autoload.folders : [""];
    return (
      <div className="space-y-4 max-w-xl">
        <SectionDivider title="Model Servers" first />
        <PathField
          label="llama-server Executable"
          value={config.api_server.llama_server_path}
          onChange={(v) => setConfig({ ...config, api_server: { ...config.api_server, llama_server_path: v } })}
          mode="executable"
          bg="gray-800"
        />
        <PathField
          label="Default Model Path"
          value={config.web_ui.default_model_path ?? ""}
          placeholder={cwd}
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, default_model_path: v } })}
          mode="dir"
          bg="gray-800"
        />
        <IntegerField
          label="Starting Port"
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

        <SectionDivider title="Autoload" />
        <ToggleField
          label="Autoload Models"
          description="Start a server for any GGUF in the model folders when a request names it, without configuring it first."
          checked={autoload.enabled}
          onChange={(v) => setAutoload({ enabled: v })}
          bg="gray-800"
        />
        {autoload.enabled && (
          <>
            <div className="space-y-2">
              {autoFolders.map((folder, i) => (
                <div key={i} className="flex items-end gap-2">
                  <div className="flex-1">
                    <PathField
                      label={i === 0 ? "Model Folders" : ""}
                      value={folder}
                      placeholder={i === 0 ? config.web_ui.default_model_path || cwd : ""}
                      onChange={(v) => setAutoload({ folders: autoFolders.map((f, j) => (j === i ? v : f)) })}
                      mode="dir"
                      startDir={config.web_ui.default_model_path}
                      bg="gray-800"
                    />
                  </div>
                  {autoFolders.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setAutoload({ folders: autoFolders.filter((_, j) => j !== i) })}
                      className="mb-1 px-2 py-1.5 text-gray-500 hover:text-red-400 transition"
                      aria-label="Remove folder"
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={() => setAutoload({ folders: [...autoFolders.filter(Boolean), ""] })}
                disabled={!autoFolders[autoFolders.length - 1]}
                className="text-xs text-blue-400 hover:text-blue-300 disabled:text-gray-600 transition"
              >
                + Add folder
              </button>
              <p className="text-xs text-gray-600">
                Requests can name <code>publisher/repo</code>, <code>publisher/repo@quant</code>, or just <code>repo</code> when only one publisher has it. A repo loads its largest quant that fits the memory budget.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <IntegerField
                label="Memory Budget"
                unit="GiB"
                tip="Leave blank to measure free memory when a model loads: free VRAM on NVIDIA GPUs, or unified memory on Apple silicon. That also accounts for other apps' models. Set a number to cap estimated use (weights, projector, KV cache) of this manager's servers instead."
                value={autoload.memory_budget_gib}
                onChange={(v) => setAutoload({ memory_budget_gib: v })}
                nullable
                min={1}
                placeholder="live"
                layout="stacked"
                bg="gray-800"
              />
              <IntegerField
                label="Reserve"
                unit="GiB"
                tip="With a live budget, memory left free for everything else when deciding whether a model fits."
                value={autoload.memory_reserve_gib}
                onChange={(v) => setAutoload({ memory_reserve_gib: v ?? defaultAutoload.memory_reserve_gib })}
                min={0}
                layout="stacked"
                bg="gray-800"
              />
              <IntegerField
                label="Max Loaded"
                tip="Autoloaded servers running at once. When a new one is needed, the one idle longest is stopped first; busy servers are never stopped."
                value={autoload.max_loaded}
                onChange={(v) => setAutoload({ max_loaded: v ?? defaultAutoload.max_loaded })}
                min={1}
                layout="stacked"
                bg="gray-800"
              />
              <IntegerField
                label="Idle Timeout"
                unit="minutes"
                tip="Stop an autoloaded server after this long without requests."
                value={autoload.idle_ttl}
                onChange={(v) => setAutoload({ idle_ttl: v ?? defaultAutoload.idle_ttl })}
                min={1}
                layout="stacked"
                bg="gray-800"
              />
              <IntegerField
                label="Parallel Slots"
                value={autoload.parallel}
                onChange={(v) => setAutoload({ parallel: v ?? defaultAutoload.parallel })}
                min={1}
                max={16}
                layout="stacked"
                bg="gray-800"
              />
              <IntegerField
                label="Priority"
                tip="When other servers here or on remote managers serve the same model, requests go to the highest priority server with a free slot."
                value={autoload.priority ?? defaultAutoload.priority}
                onChange={(v) => setAutoload({ priority: v ?? defaultAutoload.priority })}
                min={1}
                layout="stacked"
                bg="gray-800"
              />
            </div>
            {autoload.memory_budget_gib == null && (
              <p className="-mt-2 text-xs text-gray-600">
                {freeMemory
                  ? `Free now: ${(freeMemory.available_mib / 1024).toFixed(0)} of ${(freeMemory.total_mib / 1024).toFixed(0)} GiB (${freeMemory.source}).`
                  : "Free memory can't be measured on this machine; 75% of RAM is used instead."}
              </p>
            )}
            <IntegerField
              label="Context Size per Slot"
              tip="Context for autoloaded servers, capped at each model's trained context. Trained contexts are often 128k–1M tokens, whose KV cache would crowd out other models."
              value={autoload.ctx_size}
              onChange={(v) => setAutoload({ ctx_size: v ?? defaultAutoload.ctx_size })}
              min={512}
              bg="gray-800"
            />
          </>
        )}

        <SectionDivider title="Proxy" />
        <p className="text-xs text-gray-600">
          OpenAI- and Anthropic-compatible endpoints (<code>/v1/chat/completions</code>,{" "}
          <code>/v1/messages</code>, <code>/v1/models</code>) that route requests to model servers.
          Host and port changes take effect on next restart.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <TextField
            label="Host"
            value={config.api_server.host}
            onChange={(v) => setConfig({ ...config, api_server: { ...config.api_server, host: v } })}
            bg="gray-800"
          />
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Port
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

        <SectionDivider title="Slot Saves" />
        <TextField
          label="Slot Save Path"
          value={config.web_ui.slot_save_path ?? ""}
          placeholder="./slot_saves"
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, slot_save_path: v } })}
          note="Base directory for KV cache saves. Each model gets a subdirectory by model ID."
          bg="gray-800"
        />
        <SliderField
          label="Slot Storage Limit"
          value={config.web_ui.slot_storage_limit ?? null}
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, slot_storage_limit: v } })}
          sliderMin={1}
          sliderMax={100}
          placeholder="unlimited"
          nullLabel="unlimited"
          tip="Total disk space, in GB, for KV cache slot saves across all models. After each save, the least recently used saves are deleted until the total is back under the limit. Clear for no limit."
          bg="gray-800"
        />

        <SectionDivider title="Logs" />
        <IntegerField
          label="Log Buffer Size"
          value={config.web_ui.log_buffer_size}
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, log_buffer_size: v ?? config.web_ui.log_buffer_size } })}
          min={1}
          note="Takes effect on next restart."
          bg="gray-800"
        />
        <ToggleField
          label="Filter Slot Queries"
          checked={config.web_ui.filter_slot_queries ?? false}
          onChange={(v) => setConfig({ ...config, web_ui: { ...config.web_ui, filter_slot_queries: v } })}
          tip="Suppress log messages related to slot polling from llama-server backends."
          bg="gray-800"
        />

        <SectionDivider title="Manager Uplink" />
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
              layout="stacked"
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
              <PathField
                label="Model Path"
                value={model.model_path}
                onChange={pickModelPath}
                startDir={config.web_ui.default_model_path}
                note={modelFile.error ? <span className="text-amber-500">{modelFile.error}</span> : undefined}
              />

              {fileChanges && (
                <div className="flex items-start justify-between gap-3 rounded-md border border-gray-700 bg-gray-800/60 px-3 py-2 text-xs text-gray-400">
                  <span>Set from model: {fileChanges.changes.join(" · ")}</span>
                  <span className="flex shrink-0 gap-3">
                    <button
                      type="button"
                      className="text-blue-400 hover:text-blue-300"
                      onClick={() => {
                        updateModel({ ...fileChanges.before, model_path: model.model_path });
                        setFileChanges(null);
                      }}
                    >
                      Undo
                    </button>
                    <button
                      type="button"
                      className="text-gray-500 hover:text-gray-300"
                      onClick={() => setFileChanges(null)}
                      aria-label="Dismiss"
                    >
                      ✕
                    </button>
                  </span>
                </div>
              )}

              <SliderField
                label="Parallel Slots"
                value={model.parallel}
                onChange={(v) => updateModel({ parallel: v! })}
                sliderMin={1}
                sliderMax={8}
                tip={<>Number of requests this server can process concurrently. Each slot gets its own context unless Unified KV Cache is on, so more slots use more memory.<TipFlag>--parallel</TipFlag></>}
              />

              <ToggleField
                label="Unified KV Cache"
                checked={kvUnified}
                onChange={(v) => updateModel({ kv_unified: v || null })}
                tip="Pass --kv-unified to llama-server. When enabled, ctx-size is shared across all slots rather than multiplied per slot."
              />

              <SliderField
                label="Context Size per Slot"
                value={model.ctx_size}
                onChange={(v) => setCtxSize(v)}
                sliderMin={CTX_MIN}
                sliderMax={trainedCtx ?? CTX_MAX}
                snap={1024}
                placeholder={trainedCtx != null ? String(trainedCtx) : "trained"}
                nullSliderPosition={trainedCtx ?? CTX_MIN}
                nullDefault={trainedCtx ?? undefined}
                nullLabel="trained"
                tip={<>Tokens each slot can hold. Clear to use the model's trained context. The slider stops at the trained context; type a larger value only for models that support RoPE scaling.<TipFlag>--ctx-size</TipFlag></>}
                note={<>
                  {totalCtx == null
                    ? "Total context: model default"
                    : kvUnified || perSlotCtx == null
                      ? `Total context: ${totalCtx.toLocaleString()}`
                      : `Total context: ${totalCtx.toLocaleString()} (${perSlotCtx.toLocaleString()} × ${model.parallel} slots)`}
                  {trainedCtx != null && ` · Trained: ${trainedCtx.toLocaleString()}`}
                  {kvBytes != null && ` · KV cache ≈ ${formatBytes(kvBytes)} (f16)`}
                  {trainedCtx != null && model.ctx_size != null && model.ctx_size > trainedCtx && (
                    <div className="text-amber-500">Above the trained context; output may degrade unless the model supports RoPE scaling.</div>
                  )}
                  {kvBytes != null && ramBytes != null && kvBytes > ramBytes && (
                    <div className="text-amber-500">The KV cache alone is larger than system RAM ({formatBytes(ramBytes)}).</div>
                  )}
                </>}
              />

              <IntegerField
                label="GPU Layers"
                value={model.n_gpu_layers === -1 ? null : model.n_gpu_layers}
                onChange={(v) => updateModel({ n_gpu_layers: v ?? -1 })}
                min={0}
                max={modelLayers ?? undefined}
                placeholder="-1"
                note={modelLayers != null ? `Model has ${modelLayers} offloadable layers` : undefined}
                nullable
                tip={<>Number of model layers to offload to the GPU. 0 runs entirely on CPU. Clear to use -1 (offload all layers).<TipFlag>--n-gpu-layers</TipFlag></>}
              />

              <ToggleField
                label="Auto Start"
                checked={model.auto_start}
                onChange={(v) => updateModel({ auto_start: v })}
                tip="Start this model's server automatically when Llama Manager launches."
              />

              <ToggleField
                label="Allow Proxy"
                checked={model.allow_proxy ?? true}
                onChange={(v) => updateModel({ allow_proxy: v })}
                tip={<>Allow direct access to this model's llama-server (web UI, <code>/slots</code>, <code>/props</code>, etc.) through the proxy at <code>/proxy/&lt;id&gt;/</code>. Does not affect <code>/v1</code> API routing.</>}
              />

              <IntegerField
                label="Model TTL"
                tip="Stop the server after this many minutes without requests. A model with a TTL is started on demand when a request arrives, even if JIT start is disabled. Clear to keep it running indefinitely."
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

                  <SectionDivider title="Proxy" first />

                  <IntegerField
                    label="Priority"
                    tip="When multiple servers serve the same model ID, requests go to the highest priority one with a free slot."
                    value={model.priority ?? 1}
                    onChange={(v) => updateModel({ priority: v ?? 1 })}
                    min={1}
                    placeholder="1"
                  />

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

                  <SectionDivider title="Generation" />

                  <ToggleField
                    label="Use Chat Template"
                    tip={<>Apply the model's built-in Jinja2 chat template. Recommended for most models.<TipFlag>--jinja</TipFlag></>}
                    checked={adv.use_jinja ?? true}
                    onChange={(v) => updateAdv({ use_jinja: v })}
                    description={modelInfo && !modelInfo.has_chat_template ? "This model file has no built-in template." : undefined}
                  />

                  <IntegerField
                    label="Max Prediction Tokens"
                    tip={<>Cap the number of tokens generated per request. Clear to use the server default.<TipFlag>--n-predict</TipFlag></>}
                    value={adv.max_prediction_tokens}
                    onChange={(v) => updateAdv({ max_prediction_tokens: v })}
                    nullable
                    min={1}
                    placeholder="unlimited"
                  />

                  <TextField
                    label="Stop Token"
                    tip={<>Halt generation when this string appears in the output.<TipFlag>--stop</TipFlag></>}
                    value={adv.stop ?? ""}
                    placeholder="e.g. </s>"
                    onChange={(v) => updateAdv({ stop: v || null })}
                    mono
                  />

                  <SectionDivider title="Sampling" />

                  <SliderField
                    label="Temperature"
                    tip={<>Randomness of token selection. Lower = more focused, higher = more creative. Clear to use the server default.<TipFlag>--temp</TipFlag><div className="mt-1 text-gray-500">Range: 0.0–1.5 · Default: 0.8</div></>}
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
                    tip={<>Restrict selection to the smallest token set whose cumulative probability exceeds P. Lower values produce more focused output. Clear to use the server default.<TipFlag>--top-p</TipFlag><div className="mt-1 text-gray-500">Recommended: 0.8–0.99 · Default: 0.95</div></>}
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
                    tip={<>Limit selection to the K highest-probability tokens. 0 disables the filter. Clear to use the server default.<TipFlag>--top-k</TipFlag><div className="mt-1 text-gray-500">Range: 0–200 · Default: 40</div></>}
                    value={adv.top_k}
                    onChange={(v) => updateAdv({ top_k: v })}
                    sliderMin={0}
                    sliderMax={200}
                    placeholder="40"
                    nullSliderPosition={40}
                  />

                  <SliderField
                    label="Min-P Filter"
                    tip={<>Remove tokens whose probability falls below min_p × P(top token). Clear to use the server default.<TipFlag>--min-p</TipFlag><div className="mt-1 text-gray-500">Recommended: 0.0–0.05 · Default: 0.05</div></>}
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
                    tip={<>Penalise tokens that have already appeared to reduce repetition. 1.0 disables. Clear to use the server default.<TipFlag>--repeat-penalty</TipFlag><div className="mt-1 text-gray-500">Range: 1.0–2.0 · 1.0 = disabled</div></>}
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
                    tip={<>Number of recent tokens considered when applying repeat penalty. Clear to use the server default.<TipFlag>--repeat-last-n</TipFlag><div className="mt-1 text-gray-500">Range: 0–4096 · −1 = ctx size · Default: 64</div></>}
                    value={adv.repeat_last_n}
                    onChange={(v) => updateAdv({ repeat_last_n: v })}
                    sliderMin={-1}
                    sliderMax={4096}
                    placeholder="64"
                    nullSliderPosition={64}
                  />

                  <SectionDivider title="Prompt Cache" />

                  <SliderField
                    label="Slot Prompt Similarity"
                    tip={<>Route a request to the slot whose stored prompt matches at least this fraction of it. 0.0 disables similarity routing — do this when relying on the RAM prompt cache with many agents, so agents aren't routed into each other's slots. Clear to use the server default.<TipFlag>--slot-prompt-similarity</TipFlag><div className="mt-1 text-gray-500">Range: 0.0–1.0 · 0.0 = disabled · Default: 0.10</div></>}
                    value={adv.slot_prompt_similarity}
                    onChange={(v) => updateAdv({ slot_prompt_similarity: v })}
                    sliderMin={0}
                    sliderMax={100}
                    scale={100}
                    precision={2}
                    placeholder="0.10"
                    nullSliderPosition={10}
                    valueLabels={{ 0: "disabled" }}
                  />

                  <SliderField
                    label="Prompt Cache RAM (MiB)"
                    tip={<>Host-RAM ceiling for the prompt cache. Idle slots' KV state is parked here and restored on the next turn instead of re-running prefill, so many more conversations than slots can stay warm. Slider snaps to whole GiB; type an exact value in the box. Clear to use the server default.<TipFlag>--cache-ram</TipFlag><div className="mt-1 text-gray-500">−1 = no limit · 0 = disabled · Max: system RAM{totalRamMib ? ` (${totalRamMib} MiB)` : ""} · Default: 8192</div></>}
                    value={adv.cache_ram ?? null}
                    onChange={(v) => updateAdv({ cache_ram: v })}
                    sliderMin={-1}
                    sliderMax={totalRamMib ?? 131072}
                    snap={1024}
                    placeholder="8192"
                    nullSliderPosition={8192}
                    valueLabels={{ [-1]: "no limit", 0: "disabled" }}
                  />

                  <ToggleField
                    label="Cache Idle Slots"
                    tip={<>When a new task arrives, save idle slots into the RAM prompt cache (and clear them when using unified KV). Requires Prompt Cache RAM to be non-zero.<TipFlag>--cache-idle-slots / --no-cache-idle-slots</TipFlag><div className="mt-1 text-gray-500">Default: on</div></>}
                    checked={adv.cache_idle_slots ?? true}
                    onChange={(v) => updateAdv({ cache_idle_slots: v })}
                  />

                  <IntegerField
                    label="Cache Reuse Chunk"
                    unit="tokens"
                    tip={<>Minimum chunk size to try reusing from the cache via KV shifting, so a prompt that differs from the cached one in the middle can still reuse the matching chunks. Clear to use the server default.<TipFlag>--cache-reuse</TipFlag><div className="mt-1 text-gray-500">0 = disabled · Default: 0</div></>}
                    value={adv.cache_reuse ?? null}
                    onChange={(v) => updateAdv({ cache_reuse: v })}
                    min={0}
                    placeholder="0"
                    nullable
                  />

                  <SectionDivider title="Memory" />

                  <ToggleField
                    label="Auto-Fit Memory"
                    tip={<>Automatically reduce unset parameters (e.g. context size) to fit in available device memory. Disable when using KV cache slot save/restore — if the model starts with different parameters than when the slot was saved, the slot file will be incompatible.<TipFlag>--fit on|off</TipFlag><div className="mt-1 text-gray-500">Default: on</div></>}
                    checked={adv.fit ?? true}
                    onChange={(v) => updateAdv({ fit: v })}
                  />

                  <ToggleField
                    label="SWA Full Cache"
                    tip={<>Use a full-size KV cache instead of sliding window attention. Required for Gemma 2/3 when context exceeds the model's window size.<TipFlag>--swa-full</TipFlag></>}
                    checked={adv.swa_full}
                    onChange={(v) => updateAdv({ swa_full: v })}
                    description={modelInfo?.kv_cache
                      ? modelInfo.kv_cache.sliding_window
                        ? `This model uses a ${modelInfo.kv_cache.sliding_window.toLocaleString()}-token sliding window.`
                        : "This model doesn't use sliding window attention; no effect."
                      : undefined}
                  />

                  <SectionDivider title="Slot Persistence" />

                  <ToggleField
                    label="KV Cache"
                    tip={<>Persist and restore slot KV state between requests. Experimental.<TipFlag>--slots</TipFlag></>}
                    checked={adv.kv_cache}
                    onChange={(v) => updateAdv({ kv_cache: v })}
                  />

                  <TextField
                    label="Slot Save Path"
                    tip={<>Directory for KV cache slot files. Blank uses the global path from the Manager tab.<TipFlag>--slot-save-path</TipFlag></>}
                    value={adv.slot_save_path}
                    placeholder="/tmp/llama-slots"
                    onChange={(v) => updateAdv({ slot_save_path: v })}
                  />

                  <SectionDivider title="Multimodal & Speculative" />

                  <PathField
                    label="Multimodal Projector"
                    tip={<>Projector file that lets the model accept images. Usually shipped alongside vision-capable GGUFs as <code>mmproj-*.gguf</code>. Blank for text only.<TipFlag>--mmproj</TipFlag></>}
                    value={adv.mmproj_path ?? ""}
                    onChange={(v) => updateAdv({ mmproj_path: v })}
                    startDir={config.web_ui.default_model_path}
                    note={(() => {
                      const problem = projFile.error ?? projectorProblem(modelInfo, projFile.info);
                      return problem ? <span className="text-amber-500">{problem}</span> : undefined;
                    })()}
                  />

                  <PathField
                    label="Draft Model"
                    tip={<>Smaller model used for speculative decoding: it drafts tokens that the main model then verifies, which speeds up generation. Must share the main model's vocabulary. Blank to disable.<TipFlag>--model-draft</TipFlag></>}
                    value={adv.draft_model_path ?? ""}
                    onChange={(v) => updateAdv({ draft_model_path: v })}
                    startDir={config.web_ui.default_model_path}
                    note={(() => {
                      const problem = draftFile.error ?? draftProblem(modelInfo, draftFile.info);
                      return problem ? <span className="text-amber-500">{problem}</span> : undefined;
                    })()}
                  />

                  {adv.draft_model_path && (
                    <IntegerField
                      label="Draft GPU Layers"
                      tip={<>Number of draft model layers to offload to the GPU. 0 runs the draft model on CPU. Clear to use the server default.<TipFlag>--n-gpu-layers-draft</TipFlag></>}
                      value={adv.n_gpu_layers_draft ?? null}
                      onChange={(v) => updateAdv({ n_gpu_layers_draft: v })}
                      min={0}
                      max={draftLayers ?? undefined}
                      note={draftLayers != null ? `Draft model has ${draftLayers} offloadable layers` : undefined}
                      placeholder="default"
                      nullable
                    />
                  )}

                  <SectionDivider title="Server" />

                  <ToggleField
                    label="Metrics Endpoint"
                    tip={<>Expose Prometheus-compatible metrics at <code>/metrics</code> on the model's llama-server port.<TipFlag>--metrics</TipFlag></>}
                    checked={adv.metrics ?? false}
                    onChange={(v) => updateAdv({ metrics: v })}
                  />

                  <PathField
                    label="llama-server Path Override"
                    tip="Use a different llama-server binary for this model only. Blank inherits the path from the General tab."
                    value={adv.llama_server_path}
                    placeholder={config.api_server.llama_server_path || "not set"}
                    onChange={(v) => updateAdv({ llama_server_path: v })}
                    mode="executable"
                    startDir={config.api_server.llama_server_path}
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
        {config.models.length > 1 && (
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
