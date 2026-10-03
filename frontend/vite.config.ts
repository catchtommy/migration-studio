/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": process.env.STUDIO_API ?? "http://localhost:8000" } },
  test: { environment: "jsdom", setupFiles: ["./src/setupTests.ts"], globals: true, exclude: ["e2e/**", "node_modules/**"] },
});
