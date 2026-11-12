export interface ServerStatus {
  state: "unknown" | "stopped" | "starting" | "running" | "stopping" | "error" | "remote";
  pid: number | null;
  host: string | null;
  port: number | null;
  uptime: number | null;
}

export interface ModelAdvanced {
  llama_server_path: string;
  stream: boolean;
  supports_developer_role: boolean;
  slot_prompt_similarity: number | null;
  repeat_penalty: number | null;
  repeat_last_n: number | null;
  kv_cache: boolean;
  slot_save_path: string;
  swa_full: boolean;
  cache_ram?: number | null;
  cache_idle_slots?: boolean;
  cache_reuse?: number | null;
  metrics?: boolean;
  max_prediction_tokens: number | null;
  extra_args: string[];
  fit: boolean;
  use_jinja: boolean;
  temperature: number | null;
  top_p: number | null;
  top_k: number | null;
  min_p: number | null;
  stop: string | null;
  mmproj_path?: string;
  draft_model_path?: string;
  n_gpu_layers_draft?: number | null;
}

export interface ModelConfig {
  suid: string;
  type?: "local" | "remote";
  name: string | null;
  id: string | null;
  model_path: string;
  ctx_size: number | null;
  n_gpu_layers: number;
  parallel: number;
  auto_start: boolean;
  model_ttl: number | null;
  allow_proxy?: boolean;
  priority: number;
  kv_unified?: boolean | null;
  advanced: ModelAdvanced;
  remote_address?: string;
  remote_model_id?: string | null;
}

export interface WebUIConfig {
  default_model_path?: string;
  log_buffer_size: number;
  filter_slot_queries?: boolean;
  slot_save_path?: string;
  slot_storage_limit?: number | null;
  poll_server_status?: number | null;
  poll_proxy_status?: number | null;
  poll_health?: number | null;
  poll_slots?: number | null;
  poll_slots_active?: number | null;
}

export interface ApiServerConfig {
  host: string;
  port: number;
  llama_server_starting_port: number;
  llama_server_path: string;
  jit_model_server: boolean;
  jit_timeout?: number | null;
}

export interface ManagerUplinkConfig {
  enabled: boolean;
  token: string;
}

export interface RemoteManagerConfig {
  name: string | null;
  host: string;
  port: number;
  token: string;
  reconnect_interval: number;
  enabled: boolean;
}

/** Start servers on demand for GGUF files in model folders. */
export interface AutoloadConfig {
  enabled: boolean;
  /** Empty = Default Model Path. */
  folders: string[];
  /** Fixed cap on estimated use; null = measure free memory live. */
  memory_budget_gib: number | null;
  /** Live mode: memory kept free for everything else. */
  memory_reserve_gib: number;
  max_loaded: number;
  /** Minutes without requests before an autoloaded server stops. */
  idle_ttl: number;
  /** Per slot; capped at each model's trained context. */
  ctx_size: number;
  parallel: number;
  /** Rank against other servers (here or on remote managers) for the same model; highest wins. */
  priority: number;
  /** Repo id -> quant used when a request names the repo. */
  pins: Record<string, string>;
}

export interface ServerConfig {
  models: ModelConfig[];
  web_ui: WebUIConfig;
  api_server: ApiServerConfig;
  autoload?: AutoloadConfig;
  manager_uplink?: ManagerUplinkConfig;
  remote_managers?: RemoteManagerConfig[];
  manager_id?: string;
  pid_file?: string;
}

export interface RemoteModelInfo {
  suid: string;
  name: string | null;
  model_id: string;
  state: string;
  auto_start: boolean;
  has_ttl: boolean;
  allow_proxy: boolean;
  /** Started on demand from the remote's model library. */
  autoloaded?: boolean;
}

export interface UplinkStatus {
  enabled: boolean;
  connected_clients: number;
}

export interface RemoteManagerStatus {
  index: number;
  name: string | null;
  url: string;
  connection_state: "connected" | "connecting" | "disconnected";
  models: RemoteModelInfo[];
}

export interface ProxyStatus {
  state: "unknown" | "running" | "stopped";
  host: string | null;
  port: number | null;
  uptime: number | null;
  pid: number | null;
}

/** Summary of vLLM's Prometheus /metrics, standing in for llama-server's /slots. */
export interface VllmMetrics {
  requests_running: number | null;
  requests_waiting: number | null;
  /** 0..1 */
  kv_cache_usage: number | null;
  /** 0..1, since server start */
  prefix_cache_hit_rate: number | null;
  /** tokens/s since the previous scrape */
  generation_tps: number | null;
  prompt_tps: number | null;
  /** seconds, since server start */
  ttft_avg: number | null;
  /** seconds, over requests finished since the previous scrape (sticky between requests) */
  ttft_recent: number | null;
  prompt_tokens_total: number | null;
  generation_tokens_total: number | null;
  requests_finished: number | null;
  requests_errored: number | null;
  preemptions_total: number | null;
}

export type BackendKind = "llamacpp" | "vllm" | "openai";

export interface HealthStatus {
  status: string;
  slots_idle?: number;
  slots_processing?: number;
  /** Remote API models only: detected server type. */
  backend?: BackendKind;
  version?: string;
  metrics?: VllmMetrics;
}

export interface SlotInfo {
  id: number;
  id_task?: number;
  n_ctx: number;
  is_processing: boolean;
  speculative: boolean;
  params?: {
    temperature?: number;
    top_p?: number;
    min_p?: number;
    chat_format?: string;
    n_predict?: number;
    [key: string]: unknown;
  };
  next_token?: [
    {
      n_decoded?: number;
      n_remain?: number;
      has_next_token?: boolean;
    },
  ];
  prompt_progress?: number;
  prompt_n_processed?: number;
  prompt_n_total?: number;
  cancellable?: boolean;
}

export interface ModelProps {
  default_generation_settings?: Record<string, unknown>;
  total_slots?: number;
  [key: string]: unknown;
}

export interface LogMessage {
  type: "log" | "state";
  id?: number;
  text?: string;
  state?: string;
  request_id?: string;
}

export interface RequestLogEntry {
  request_id: string;
  timestamp: number;
  model_id: string | null;
  request_headers: Record<string, string>;
  request_body: Record<string, unknown> | null;
  response_status: number | null;
  response_headers: Record<string, string> | null;
  response_body: Record<string, unknown> | string | null;
  streaming: boolean;
  elapsed: number | null;
}

/** A server started on demand from the model library (not in config). */
export interface AutoloadedServer {
  suid: string;
  /** Library quant id, e.g. "lmstudio-community/qwen3.5-27b@q4_k_m". */
  name: string;
  model_id: string;
  model_path: string;
  ctx_size: number | null;
  parallel: number;
  idle_ttl: number | null;
  memory_mib: number | null;
  idle_seconds: number | null;
  state: ServerStatus["state"];
  pid: number | null;
  host: string | null;
  port: number | null;
  uptime: number | null;
}
