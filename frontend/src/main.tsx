import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as ReactJsxRuntime from "react/jsx-runtime";
import { RouterProvider } from "react-router-dom";

import { installChunkReload } from "./lib/chunkLoad";
import { installDeckGlobal } from "./lib/deckGlobal";
import { refreshAccessToken, refreshAccessTokenResult } from "./lib/extensionToken";
import { migrateLegacyStorage } from "./lib/legacyStorage";
import { router } from "./routes/router";
import { useAuthStore } from "./state/auth";
import { useBrandingStore } from "./state/branding";
import { confirmDialog, promptDialog } from "./state/dialogs";
import "./styles/index.css";

// Gemerkte Einstellungen dieses Browsers unter ihren neuen Namen uebernehmen, BEVOR irgendetwas
// rendert und sie liest (lib/legacyStorage.ts). Wirft nie.
migrateLegacyStorage();

// Import-Map-Shim (docs/02-EXTENSION-API.md §5): EINE React-Instanz fuer das
// Kern-Bundle UND jedes per `import()` nachgeladene Extension-Bundle -- die Shims
// unter public/lattice-shim/*.js lesen genau diese Felder (siehe dort). React 18
// trennt "react-dom" (createPortal, flushSync, …) und "react-dom/client"
// (createRoot) in zwei Pakete -- zu EINEM Namespace zusammengefuehrt, damit ein
// einziger `react-dom.js`-Shim beide bedienen kann.
const mergedReactDom = { ...ReactDOM, ...ReactDOMClient };

// Window.__nodvardDeck (alt: Window.__lattice) ist in ./global.d.ts deklariert (geteilt mit
// extensions/tsconfig.json, siehe dort). Beide Namen zeigen auf DASSELBE Objekt
// (lib/deckGlobal.ts): neue Bundles lesen `__nodvardDeck ?? __lattice`, aeltere nur `__lattice`.
installDeckGlobal({
  React,
  ReactDOM: mergedReactDom,
  ReactJsxRuntime,
  // Extension-Seiten (z. B. die Node-Seite der proxmox-Extension) brauchen
  // authentifizierte Kern-Endpunkte (GET /hosts, POST /hosts/{id}/actions/...), aber
  // ein Extension-Bundle hat keinen Zugriff auf state/auth.ts (nur React/ReactDOM/
  // die SDK-Typen sind ueber den Import-Map-Shim geteilt, docs/02 §5). Ein LIVE-Getter
  // statt eines einmalig kopierten Werts, damit ein Token-Refresh (state/auth.ts)
  // sofort sichtbar ist, ohne dass die Extension-Seite neu geladen werden muss.
  getAccessToken: () => useAuthStore.getState().accessToken,
  // Nur mit dem aktuellen Token bekaemen Extension-Seiten nach 15 min Leerlauf
  // "Nicht authentifiziert". Mit dem Wert hier holt ihr gemeinsames
  // authedFetch bei 401 selbst ein neues. refresh() haengt sich an einen schon
  // laufenden Aufruf an (state/auth.ts), parallele Seiten rotieren das Cookie also
  // nicht gegeneinander.
  refreshAccessToken,
  // Dasselbe mit Grund, wenn es nicht klappt: "abgelehnt" (abgemeldet) oder "Server antwortet
  // gerade nicht" (Neustart nach einem Deploy) -- die Seiten zeigen dann die passende Meldung.
  refreshAccessTokenResult,
  // Extension-Seiten haben aus demselben Grund wie oben keinen Zugriff auf
  // state/dialogs.ts ueber den Import-Map-Shim -- ohne diese Funktionen blieben nur
  // window.confirm()/prompt() oder destruktive Aktionen ganz ohne Rueckfrage.
  // Dieselben Funktionen wie GlobalDialogs (in AppShell.tsx gemountet) durchreichen,
  // statt eine zweite Dialog-Implementierung zu bauen.
  confirmDialog,
  promptDialog,
  // Extension-Seiten loesen Aktionen ueber POST /hosts/{id}/actions/{type} aus, was
  // bei autonomy.mode=propose (Default) immer NUR einen Vorschlag anlegt
  // (status=proposed) -- die Freigabe ist ein zweiter, separater Aufruf
  // (POST /actions/{id}/approve). Extension-Seiten pruefen damit VOR einem
  // automatischen Nach-Bestaetigen, ob der angemeldete Nutzer die noetige
  // actions.approve:<risk>-Berechtigung ueberhaupt hat --
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

// Vor dem ersten Render angestossen (nicht awaitet -- GET /branding ist unauth. und
// schnell, ein Render mit den eingebauten CSS-Defaults fuer einen Frame ist kein
// Problem, ein bewusst blockierender Start-Ladebildschirm dafuer waere Overkill):
// Login- UND Setup-Seite sollen von Anfang an das echte Branding zeigen, nicht erst
// nach einem erfolgreichen Login (docs/01-ARCHITECTURE.md §6).
void useBrandingStore.getState().load();

ReactDOMClient.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
