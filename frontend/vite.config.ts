import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev server proxies the API (and WebSocket) to one backend process.
const api = process.env.VITE_API_TARGET ?? "http://localhost:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: api, ws: true } },
  },
  build: { sourcemap: true },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
