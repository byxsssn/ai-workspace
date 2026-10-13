import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const proxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  // Never load the backend's .env into the client or expose it through /@fs/.
  envDir: ".",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy,
    fs: { strict: true, allow: ["."] },
    headers: { "Referrer-Policy": "no-referrer" },
  },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
});
