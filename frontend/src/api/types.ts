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
  slot_prompt_similarity: number | null;
  repeat_penalty: number | null;
  repeat_last_n: number | null;
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
  "auto-start": boolean;
  "model-ttl": number | null;
  advanced: ModelAdvanced;
  "remote-address"?: string;
  "remote-model-id"?: string | null;
}

export interface WebUIConfig {
  log_buffer_size: number;
}

export interface ApiServerConfig {
  host: string;
  port: number;
  "llama-server-starting-port": number;
  "llama-server-path": string;
  "jit-model-server": boolean;
  "jit-timeout"?: number | null;
}

export interface ServerConfig {
  models: ModelConfig[];
  "web-ui": WebUIConfig;
  "api-server": ApiServerConfig;
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
