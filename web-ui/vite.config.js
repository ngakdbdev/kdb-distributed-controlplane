import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev-mode proxy so the UI can call /api/* without CORS friction while
// iterating locally. In production the control API sits behind the same
// reverse proxy/domain - see deploy/gcp/04_deploy_stack.sh.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
