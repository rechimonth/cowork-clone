import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El dev server corre en 1420 para coincidir con DEFAULT_CORS_ORIGINS del
// backend (`http://localhost:1420` y `http://127.0.0.1:1420`). Cambiar el
// puerto sin actualizar esa lista rompe el CORS.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
