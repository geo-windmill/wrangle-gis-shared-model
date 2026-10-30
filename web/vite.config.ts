import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the UI runs on :5173 and forwards /api to `wrangle serve` on :8000.
// In production `wrangle serve` hands out the built files itself.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
