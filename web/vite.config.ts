/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // antd 整包约 800KB+，拆包后仍会超默认 500KB 阈值；业务代码已与 vendor 分离。
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return;
          }
          if (id.includes("antd") || id.includes("@ant-design") || id.includes("/rc-")) {
            return "antd";
          }
          if (id.includes("sip.js")) {
            return "sip";
          }
          if (
            id.includes("node_modules/react-dom") ||
            id.includes("node_modules\\react-dom") ||
            id.includes("node_modules/react/") ||
            id.includes("node_modules\\react\\") ||
            id.includes("node_modules/scheduler") ||
            id.includes("node_modules\\scheduler")
          ) {
            return "react";
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://127.0.0.1:8080",
      "/cdr": "http://127.0.0.1:8080",
      "/releases": "http://127.0.0.1:8080",
      "/ivr": "http://127.0.0.1:8080",
      "/media": "http://127.0.0.1:8080",
      "/extensions": "http://127.0.0.1:8080",
      "/gateways": "http://127.0.0.1:8080",
      "/numbers": "http://127.0.0.1:8080",
      "/entries": "http://127.0.0.1:8080",
      "/skill-groups": "http://127.0.0.1:8080",
      "/dial-tasks": "http://127.0.0.1:8080",
      "/blacklist": "http://127.0.0.1:8080",
      "/ai-scenes": "http://127.0.0.1:8080",
      "/fs-nodes": "http://127.0.0.1:8080",
      "/users": "http://127.0.0.1:8080",
      "/calls": "http://127.0.0.1:8080",
      "/agent": "http://127.0.0.1:8080",
      "/registrations": "http://127.0.0.1:8080",
      "/ws": {
        target: "http://127.0.0.1:8082",
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
