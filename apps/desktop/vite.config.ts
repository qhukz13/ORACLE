/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.ORACLE_BACKEND ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  // Fixed port: the Tauri shell points at it, and a shifting port is a bad
  // first-run experience.
  server: {
    port: 5273,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true, ws: true },
      "/health": { target: BACKEND, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "happy-dom",
    setupFiles: ["./vitest.setup.ts"],
  },
});
