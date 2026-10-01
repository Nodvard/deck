import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Proxy auf :8080 im Entwicklungsbetrieb (docs/00-DECISIONS.md D-03: das Frontend wird
// im Produktivbetrieb vom Backend als StaticFiles ausgeliefert -- ein Port, ein Dienst.
// Im Dev-Modus laufen zwei Server, deshalb der Proxy statt CORS-Konfiguration).
export default defineConfig({
  plugins: [react()],
  // Kennung dieses Builds: haengt an den Extension-Bundles, damit der Browser nach
  // einem Update die neuen Seiten laedt statt der zwischengespeicherten alten.
  define: { __BUILD_ID__: JSON.stringify(Date.now().toString(36)) },
  // Konsolen-Auftrag: noVNC (>= 1.5) nutzt Top-Level-`await` (core/util/browser.js,
  // WebCodecs-Pruefung) -- Vites Default-Ziel (es2020) kann das nicht ausgeben und
  // bricht den Build ab. es2022 koennen alle Browser, die Lattice ohnehin braucht.
  build: { target: "es2022" },
  optimizeDeps: { esbuildOptions: { target: "es2022" } },
  server: {
    proxy: {
      // ws: true -- /api/v1/ws (Multiplex-Hub, WP-6) und /api/v1/ws/terminal/* sind
      // WebSocket-Upgrades; ohne dieses Flag proxyt Vite nur normales HTTP und der
      // Upgrade-Handshake schlaegt im Dev-Betrieb fehl (im Produktivbetrieb kein
      // Thema, da Backend und Frontend dort denselben Port teilen, D-03).
      "/api": { target: "http://127.0.0.1:8080", ws: true },
    },
  },
});
