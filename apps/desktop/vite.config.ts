/// <reference types="vitest" />
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";
import { defineConfig, type Plugin, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.ORACLE_BACKEND ?? "http://127.0.0.1:8787";

/**
 * Lets `bench/graph-render.html` write its own result to `logs/measurements/`.
 *
 * OQ-22's measurement 2 has to run in a window that is actually being composited, and the only
 * windows that qualify here are ones no test runner drives — the app's browser pane, or the Tauri
 * shell. Without this, collecting the numbers means a human copying JSON out of a page, which is
 * how a measurement ends up transcribed wrong or not at all.
 *
 * **Dev-server only, and it writes exactly one path.** It is not a general file-write endpoint:
 * the name is fixed here, not taken from the request, so the page cannot choose where its bytes
 * land. `apply: "serve"` keeps it out of `vite build` entirely.
 */
function benchResultSink(): Plugin {
  const DIR = resolve(import.meta.dirname, "../../logs/measurements");
  const PATHS: Record<string, string> = {
    // The frame measurements.
    "/bench-result": resolve(DIR, "oq22-render.json"),
    // Proof that the graph is actually mounted, written when the page goes idle. Without it, an
    // idle-CPU sample cannot tell "1,420 nodes sitting still" from "a page that failed to load" —
    // both are quiet, and only one of them is the measurement.
    "/bench-idle": resolve(DIR, "oq22-idle-marker.json"),
  };
  return {
    name: "oracle:bench-result-sink",
    apply: "serve",
    configureServer(server) {
      for (const [route, out] of Object.entries(PATHS)) {
        server.middlewares.use(route, sink(server, out));
      }
    },
  };

  function sink(server: ViteDevServer, OUT: string) {
    return (req: IncomingMessage, res: ServerResponse): void => {
      if (req.method !== "POST") {
        res.statusCode = 405;
        res.end("POST only");
        return;
      }
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        try {
          // Parsed before it is written: a result file that is not JSON is worse than no file,
          // because it is discovered later, by whoever is trying to quote the numbers.
          const parsed: unknown = JSON.parse(body);
          mkdirSync(dirname(OUT), { recursive: true });
          writeFileSync(OUT, JSON.stringify(parsed, null, 2), "utf8");
          server.config.logger.info(`  bench result written to ${OUT}`);
          res.statusCode = 200;
          res.end(OUT);
        } catch (err) {
          res.statusCode = 400;
          res.end(String(err));
        }
      });
    };
  }
}

export default defineConfig({
  plugins: [react(), benchResultSink()],
  // Fixed port: the Tauri shell points at it, and a shifting port is a bad
  // first-run experience.
  server: {
    port: 5273,
    strictPort: true,
    // `src-tauri/target` is a 2.4 GB Rust build tree, and `tauri dev` rewrites the binary inside it
    // while the dev server is up. Watching it is pure cost with one sharp edge: the watcher opens
    // the `.exe` mid-link and dies with EBUSY, taking the whole dev server down and leaving the
    // Tauri window it was serving pointed at nothing. Nothing under it is a frontend source.
    watch: { ignored: ["**/src-tauri/**"] },
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
