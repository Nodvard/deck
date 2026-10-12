/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { kitDedupe, kitFsAllow } from "./kit.resolve";

// Analog zu extensions/tsconfig.json: dieselbe
// Modulaufloesungs-Huerde wie dort, nur zur Laufzeit statt nur fuer Typen. Bewusst
// eine EIGENE Config statt vitest.config.ts' `include` zu erweitern, damit `npm test`
// weiterhin nur den Kern prueft (wie `typecheck` vs. `typecheck:extensions`) --
// separater Befehl `npm run test:extensions`. Muss hier in frontend/ liegen (nicht in
// extensions/, das kein eigenes node_modules hat) -- sonst scheitert schon das Laden
// dieser Config-Datei an "vitest/config"/"@vitejs/plugin-react".
export default defineConfig({
  plugins: [react()],
  // Vites Dev-Server/vite-node weigert sich standardmaessig, Dateien ausserhalb des
  // Projekt-Roots (hier frontend/) auszuliefern (fs.allow) -- die Testdateien liegen
  // aber bewusst bei ihrer jeweiligen Extension, nicht in frontend/.
  server: { fs: { allow: kitFsAllow } },
  resolve: { dedupe: kitDedupe },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    include: ["../extensions/*/frontend/src/**/*.test.{ts,tsx}"],
  },
});
