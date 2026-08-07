import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// UI for the Tauri desktop shell. Dev server is only used inside the
// native window (tauri.conf.json → devUrl), not as a standalone website.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    host: "127.0.0.1",
    hmr: { protocol: "ws", host: "127.0.0.1", port: 5173 },
  },
  build: {
    outDir: "../ui",
    emptyOutDir: true,
  },
});
