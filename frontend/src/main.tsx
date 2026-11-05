import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { installStyleNonce } from "../../extensions/_shared/frontend/src/csp";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as ReactJsxRuntime from "react/jsx-runtime";
import { RouterProvider } from "react-router-dom";

import { clearQueriesOnAccountChange } from "./lib/accountCache";
import { installChunkReload } from "./lib/chunkLoad";
import { installDeckGlobal } from "./lib/deckGlobal";
import { refreshAccessToken, refreshAccessTokenResult } from "./lib/extensionToken";
import { loadCustomCss } from "./lib/customCss";
import { applyTheme, storedTheme } from "./lib/theme";
import { router } from "./routes/router";
import { useAuthStore } from "./state/auth";
import { useBrandingStore } from "./state/branding";
import { useEditionStore } from "./state/edition";
import { confirmDialog, confirmIdentity, promptDialog } from "./state/dialogs";
import "./styles/index.css";

// Vor dem ersten Rendern, damit die Seite nicht kurz im falschen Theme aufblitzt.
installStyleNonce();
applyTheme(storedTheme());
loadCustomCss();

// Import-Map-Shim (docs/02-EXTENSION-API.md §5): EINE React-Instanz für das
// Kern-Bundle UND jedes per `import()` nachgeladene Extension-Bundle -- die Shims
// unter public/nodvard-deck-shim/*.js lesen genau diese Felder (siehe dort). React 18
// trennt "react-dom" (createPortal, flushSync, …) und "react-dom/client"
// (createRoot) in zwei Pakete -- zu EINEM Namespace zusammengeführt, damit ein
// einziger `react-dom.js`-Shim beide bedienen kann.
const mergedReactDom = { ...ReactDOM, ...ReactDOMClient };

// Window.__nodvardDeck ist in ./global.d.ts deklariert (geteilt mit extensions/tsconfig.json, siehe dort).
installDeckGlobal({
  React,
  ReactDOM: mergedReactDom,
  ReactJsxRuntime,
  // Extension-Seiten (z. B. die Node-Seite der proxmox-Extension) brauchen
  // authentifizierte Kern-Endpunkte (GET /hosts, POST /hosts/{id}/actions/...), aber
  // ein Extension-Bundle hat keinen Zugriff auf state/auth.ts (nur React/ReactDOM/
  // die SDK-Typen sind über den Import-Map-Shim geteilt, docs/02 §5). Ein LIVE-Getter
  // statt eines einmalig kopierten Werts, damit ein Token-Refresh (state/auth.ts)
  // sofort sichtbar ist, ohne dass die Extension-Seite neu geladen werden muss.
  getAccessToken: () => useAuthStore.getState().accessToken,
  getProductName: () => useEditionStore.getState().edition.product_name,
  // Nur mit dem aktuellen Token bekämen Extension-Seiten nach 15 min Leerlauf
  // "Nicht authentifiziert". Mit dem Wert hier holt ihr gemeinsames
  // authedFetch bei 401 selbst ein neues. refresh() hängt sich an einen schon
  // laufenden Aufruf an (state/auth.ts), parallele Seiten rotieren das Cookie also
  // nicht gegeneinander.
  refreshAccessToken,
  // Dasselbe mit Grund, wenn es nicht klappt: "abgelehnt" (abgemeldet) oder "Server antwortet
  // gerade nicht" (Neustart nach einem Deploy) -- die Seiten zeigen dann die passende Meldung.
  refreshAccessTokenResult,
  // Extension-Seiten haben aus demselben Grund wie oben keinen Zugriff auf
  // state/dialogs.ts über den Import-Map-Shim -- ohne diese Funktionen blieben nur
  // window.confirm()/prompt() oder destruktive Aktionen ganz ohne Rückfrage.
  // Dieselben Funktionen wie GlobalDialogs (in AppShell.tsx gemountet) durchreichen,
  // statt eine zweite Dialog-Implementierung zu bauen.
  confirmDialog,
  promptDialog,
  // Passwort und Code, wenn ein Aufruf gespeicherte Zugangsdaten ersetzt oder löscht (403 `confirm_required`).
  confirmIdentity,
  // Extension-Seiten lösen Aktionen über POST /hosts/{id}/actions/{type} aus, was
  // bei autonomy.mode=propose (Default) immer NUR einen Vorschlag anlegt
  // (status=proposed) -- die Freigabe ist ein zweiter, separater Aufruf
  // (POST /actions/{id}/approve). Extension-Seiten prüfen damit VOR einem
  // automatischen Nach-Bestätigen, ob der angemeldete Nutzer die nötige
  // actions.approve:<risk>-Berechtigung überhaupt hat --
  // ein Live-Getter wie getAccessToken() aus demselben Grund (Rollenwechsel ohne
  // Neuladen sichtbar).
  hasPermission: (permission: string) => useAuthStore.getState().hasPermission(permission),
});

// Nach einem Deploy fehlen die alten Chunks (xterm, noVNC) -- statt eines
// Fehlers bis zum Strg+R die Seite einmal selbst neu laden (lib/chunkLoad.ts).
installChunkReload();

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});
clearQueriesOnAccountChange(queryClient);

// Vor dem ersten Render angestoßen (nicht awaitet -- GET /branding ist unauth. und
// schnell, ein Render mit den eingebauten CSS-Defaults für einen Frame ist kein
// Problem, ein bewusst blockierender Start-Ladebildschirm dafür wäre Overkill):
// Login- UND Setup-Seite sollen von Anfang an das echte Branding zeigen, nicht erst
// nach einem erfolgreichen Login (docs/01-ARCHITECTURE.md §6).
void useBrandingStore.getState().load();
void useEditionStore.getState().load();

ReactDOMClient.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
