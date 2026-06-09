import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        configure: (proxy) => {
          proxy.on("error", () => {});
        },
      },
      "/ws": {
        target: "http://127.0.0.1:8000",
        ws: true,
        configure: (proxy) => {
          proxy.on("error", () => {});
        },
      },
      "/v2": {
        target: "http://127.0.0.1:8000",
        ws: true,
        configure: (proxy) => {
          proxy.on("error", () => {});
        },
      },
    },
  },
});
