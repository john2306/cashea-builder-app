import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El proxy reenvía /api y /auth al backend de FastAPI en desarrollo,
// así el frontend usa rutas relativas y no hay problemas de CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    // El builder corre en 5180; el puerto 5173 queda para Traefik (apps desplegadas).
    port: 5180,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
