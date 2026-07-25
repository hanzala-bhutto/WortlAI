import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Paths the backend owns. Proxying them keeps the browser on one origin in
// development, matching production where FastAPI serves the built app. So there
// is no CORS difference between dev and prod, and the voice WebSocket added in
// issue #3 is same-origin either way.
const BACKEND = "http://localhost:8000";

// /api carries the WebSocket too: the voice socket is a domain route, so it is
// versioned like the rest (/api/v1/voice/stream), not parked on its own prefix.
const BACKEND_PATHS = ["/api", "/health", "/readyz"];
const WEBSOCKET_PATHS = new Set(["/api"]);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 3001: the self-hosted Langfuse UI holds 3000.
    port: 3001,
    strictPort: true,
    proxy: Object.fromEntries(
      BACKEND_PATHS.map((path) => [
        path,
        { target: BACKEND, changeOrigin: true, ws: WEBSOCKET_PATHS.has(path) },
      ]),
    ),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
