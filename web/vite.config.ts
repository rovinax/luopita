import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:5170",
      "/health": "http://127.0.0.1:5170",
      "/webhooks": "http://127.0.0.1:5170",
      "/chat": "http://127.0.0.1:5170",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
