export interface ServerStatus {
  state: "stopped" | "starting" | "running" | "stopping" | "error" | "remote";
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
  extra_args: string[];
}

export interface ModelConfig {
  type?: "local" | "remote";
  name: string | null;
  id: string | null;
  model_path: string;
  ctx_size: number;
  n_gpu_layers: number;
  parallel: number;
  auto_start: boolean;
  model_ttl: number | null;
  advanced: ModelAdvanced;
  remote_address?: string;
  remote_model_id?: string | null;
}

export interface WebUIConfig {
  log_buffer_size: number;
  slot_save_path?: string;
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

export interface ServerConfig {
  models: ModelConfig[];
  web_ui: WebUIConfig;
  api_server: ApiServerConfig;
}

export interface ProxyStatus {
  state: "running" | "stopped";
  host: string | null;
  port: number | null;
  uptime: number | null;
  pid: number | null;
}

export interface HealthStatus {
  status: string;
  slots_idle?: number;
  slots_processing?: number;
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
}
