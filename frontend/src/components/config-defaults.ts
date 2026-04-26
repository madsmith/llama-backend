import type { ServerConfig, RemoteManagerConfig } from "../api/types";

export type SettingsTab = string;

export const defaultRemoteManager: RemoteManagerConfig = {
  name: null,
  host: "",
  port: 8000,
  token: "",
  reconnect_interval: 5,
  enabled: true,
};

export const defaultConfig: ServerConfig = {
  models: [
    {
      suid: "",
      type: "local",
      name: null,
      id: null,
      model_path: "",
      ctx_size: 65536,
      n_gpu_layers: -1,
      parallel: 2,
      auto_start: false,
      model_ttl: null,
      priority: 1,
      kv_unified: null,
      advanced: {
        llama_server_path: "",
        stream: true,
        supports_developer_role: false,
        slot_prompt_similarity: null,
        repeat_penalty: null,
        repeat_last_n: null,
        kv_cache: false,
        slot_save_path: "",
        swa_full: false,
        max_prediction_tokens: null,
        extra_args: [],
        fit: false,
        use_jinja: false,
        temperature: null,
        top_p: null,
        top_k: null,
        min_p: null,
        stop: null,
      },
      remote_address: "",
      remote_model_id: null,
    },
  ],
  web_ui: {
    log_buffer_size: 10_000,
    filter_slot_queries: false,
    slot_save_path: "",
    poll_server_status: null,
    poll_proxy_status: null,
    poll_health: null,
    poll_slots: null,
    poll_slots_active: null,
  },
  api_server: {
    host: "0.0.0.0",
    port: 1234,
    llama_server_starting_port: 3210,
    llama_server_path: "",
    jit_model_server: true,
  },
  manager_uplink: {
    enabled: false,
    token: "",
  },
  remote_managers: [],
};
