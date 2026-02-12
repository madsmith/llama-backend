export interface ServerStatus {
  state: "stopped" | "starting" | "running" | "stopping" | "error";
  pid: number | null;
  host: string | null;
  port: number | null;
  uptime: number | null;
}

export interface ServerConfig {
  llama_server_path: string;
  model_path: string;
  host: string;
  port: number;
  ctx_size: number;
  n_gpu_layers: number;
  parallel: number;
  stream: boolean;
  slot_prompt_similarity: number | null;
  repeat_penalty: number | null;
  repeat_last_n: number | null;
  slot_save_path: string;
  swa_full: boolean;
  extra_args: string[];
  log_buffer_size: number;
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
