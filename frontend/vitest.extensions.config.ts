/// <reference types="vitest/config" />
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

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
  server: { fs: { allow: [path.resolve(__dirname, "..")] } },
  resolve: {
    alias: {
      "react-dom/client": path.resolve(__dirname, "node_modules/react-dom/client"),
      "react-dom": path.resolve(__dirname, "node_modules/react-dom"),
      "react/jsx-runtime": path.resolve(__dirname, "node_modules/react/jsx-runtime"),
      "react/jsx-dev-runtime": path.resolve(__dirname, "node_modules/react/jsx-dev-runtime"),
      react: path.resolve(__dirname, "node_modules/react"),
      "react-router-dom": path.resolve(__dirname, "node_modules/react-router-dom"),
      "@testing-library/react": path.resolve(__dirname, "node_modules/@testing-library/react"),
      "@testing-library/jest-dom": path.resolve(__dirname, "node_modules/@testing-library/jest-dom"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    include: ["../extensions/*/frontend/src/**/*.test.{ts,tsx}"],
  },
});
