import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-mode proxy so `npm run dev` can talk to a locally-running backend
// (`uvicorn backend.main:app --port 8002`) without a CORS dance. In
// production the built SPA is served by nginx, which does the same
// /api -> backend proxying (see nginx.conf) — the frontend code always just
// calls relative /api/... paths, in dev and in prod alike.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8002", changeOrigin: true },
    },
  },
});
