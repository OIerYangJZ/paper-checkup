import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `base: "./"` keeps the build relocatable (served by FastAPI at "/" or from any static host / sub-path).
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
