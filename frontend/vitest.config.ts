/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Separat von vite.config.ts (nicht per mergeConfig zusammengefuehrt), damit der
// Dev-Server-Proxy (der einen laufenden Backend-Prozess auf :8080 voraussetzt) fuer
// Unit-Tests irrelevant bleibt -- Tests importieren Komponenten direkt, kein Netzwerk.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
