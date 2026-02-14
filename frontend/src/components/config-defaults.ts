import type { ServerConfig } from "../api/types";

export type SettingsTab = string;

export const defaultConfig: ServerConfig = {
  models: [
    {
      type: "local",
      name: null,
      id: null,
      model_path: "",
      ctx_size: 65536,
      n_gpu_layers: -1,
      parallel: 2,
      auto_start: false,
      model_ttl: null,
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
        extra_args: [],
      },
      remote_address: "",
      remote_model_id: null,
    },
  ],
  web_ui: {
    log_buffer_size: 10_000,
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
};
