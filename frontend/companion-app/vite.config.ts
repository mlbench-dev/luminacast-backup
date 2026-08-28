import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash]-v2.js",
        chunkFileNames: "assets/[name]-[hash]-v2.js",
        // Keep the heavy, rarely-changing libs in their own chunks so they
        // aren't bundled into the entry (parsed + resident on every page).
        // Remotion is only pulled in by the lazy Cast Builder route now.
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("/@remotion/") || /\/remotion\//.test(id)) return "remotion";
          if (id.includes("/react-dom/") || /\/react\//.test(id) || id.includes("/scheduler/")) return "react";
          if (id.includes("/@tanstack/")) return "tanstack";
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
});
