/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
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
