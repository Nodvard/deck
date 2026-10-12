/**
 * Wie Dateien ausserhalb von frontend/ ihre Pakete finden: das UI-Kit (extensions/_shared/frontend/src) und die
 * Seiten der Erweiterungen liegen unter extensions/, das kein eigenes node_modules hat. `dedupe` laesst Vite
 * diese Pakete immer aus frontend/node_modules laden, egal wo die importierende Datei liegt (dieselbe Kopie wie
 * der Kern, kein zweites React). Ein Ort fuer alle, die solche Dateien bauen oder testen: der Kern
 * (vite.config.ts, vitest.config.ts), die Erweiterungs-Tests (vitest.extensions.config.ts) und die
 * Design-Vorschau (vite.preview.config.ts). Fuer die Typen gilt dasselbe ueber `paths` in tsconfig.json bzw.
 * extensions/tsconfig.json.
 *
 * Bewusst ohne `node:path`: vite.config.ts wird mit `tsc -b` geprueft, und es gibt keine Node-Typen.
 */
export const kitDedupe = [
  "react",
  "react-dom",
  "react-router-dom",
  "@testing-library/react",
  "@testing-library/jest-dom",
];

/** Vites Dev-Server und vite-node liefern sonst keine Dateien ausserhalb von frontend/ aus (relativ zu frontend/). */
export const kitFsAllow = [".", "../extensions"];
