import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: { proxy: { "/api": "http://localhost:8000", "/ws": { target: "ws://localhost:8000", ws: true } } },
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id: string) => {
          if (id.includes("node_modules")) {
            if (/[\\/]react(?:-dom|-router-dom)?[\\/]/.test(id)) return "vendor-react";
            if (id.includes("recharts")) return "vendor-charts";
            if (id.includes("@radix-ui") || id.includes("axios")) return "vendor-ui";
          }
          return undefined;
        },
      },
    },
  },
});
