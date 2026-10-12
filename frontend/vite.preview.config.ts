import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { kitDedupe, kitFsAllow } from "./kit.resolve";

/**
 * NUR fuer die Design-Vorschau (preview.html): wie vite.config.ts, dazu dieselbe
 * Aufloesung wie vitest.extensions.config.ts -- die Vorschau rendert auch die
 * Extension-Seiten direkt aus ihren Quellen (extensions/<id>/frontend/src), und die
 * liegen ausserhalb von frontend/ und haben kein eigenes node_modules.
 *
 *   node node_modules/vite/bin/vite.js --config vite.preview.config.ts --port 5199
 */
export default defineConfig({
  plugins: [react()],
  build: { target: "es2022" },
  optimizeDeps: { esbuildOptions: { target: "es2022" } },
  server: { fs: { allow: kitFsAllow } },
  resolve: { dedupe: kitDedupe },
});
