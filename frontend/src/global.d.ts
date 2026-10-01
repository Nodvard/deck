// Ambient global fuer den Import-Map-Shim (docs/02-EXTENSION-API.md §5,
// window.__nodvardDeck, alt: window.__lattice). Eigene Datei statt eines inline `declare global` in
// main.tsx, damit extensions/tsconfig.json (tsc gegen die Extension-Frontends,
// die main.tsx selbst nie importieren) dieselbe Ambient-Deklaration einbinden
// kann, ohne main.tsx als Modul mitzuziehen.
import type * as React from "react";
import type * as ReactDOM from "react-dom";
import type * as ReactDOMClient from "react-dom/client";
import type * as ReactJsxRuntime from "react/jsx-runtime";

declare global {
  /** Build-Kennung (vite.config.ts `define`) -- in Tests nicht gesetzt. */
  const __BUILD_ID__: string;
  /** Ergebnis von `window.__nodvardDeck.refreshAccessTokenResult()` (lib/extensionToken.ts). */
  type NodvardDeckTokenRefreshResult =
    | { status: "ok"; token: string }
    /** Der Server lehnt die Anmeldung ab: wirklich abgemeldet. */
    | { status: "rejected" }
    /** Der Server antwortet gerade nicht (Neustart, WLAN weg) oder mit einem Fehler: die Anmeldung
     * bleibt. `httpStatus`/`message` (Status und Grund des Servers, z. B. 507 "Speicherplatz voll")
     * gibt es nur, wenn er wirklich geantwortet hat -- aeltere Kerne kennen sie nicht. */
    | { status: "unavailable"; httpStatus?: number; message?: string };
  /** Alter Name von `NodvardDeckTokenRefreshResult`, gilt weiter (Uebergang). */
  type LatticeTokenRefreshResult = NodvardDeckTokenRefreshResult;
  /**
   * Das globale Objekt der Kern-Shell (main.tsx). Es haengt unter zwei Namen am Fenster
   * (`window.__nodvardDeck`, alt: `window.__lattice`), und beide zeigen auf DASSELBE Objekt.
   * Erweiterungscode holt es ueber `deck()` bzw. `findDeck()` (lib/deckGlobal.ts) statt ueber
   * einen der Namen: ein neues Bundle in einem Tab mit aelterem Kern findet nur den alten.
   */
  interface NodvardDeckShell {
    React: typeof React;
    ReactDOM: typeof ReactDOM & typeof ReactDOMClient;
    ReactJsxRuntime: typeof ReactJsxRuntime;
    getAccessToken: () => string | null;
    /** Holt ein neues Token (Refresh-Cookie) und liefert es, bei Fehlschlag null (wirft nie).
     * Optional, damit Test-Attrappen nicht alle nachziehen muessen -- die Kern-Shell setzt es immer. */
    refreshAccessToken?: () => Promise<string | null>;
    /** Wie `refreshAccessToken`, sagt aber, warum es nicht geklappt hat: abgelehnt oder Server
     * gerade nicht erreichbar (wirft nie). Optional: aeltere Kerne haben es noch nicht. */
    refreshAccessTokenResult?: () => Promise<NodvardDeckTokenRefreshResult>;
    confirmDialog: (message: string, options?: { title?: string; danger?: boolean; confirmLabel?: string; cancelLabel?: string }) => Promise<boolean>;
    promptDialog: (message: string, defaultValue?: string) => Promise<string | null>;
    hasPermission: (permission: string) => boolean;
    /** Setzt die Kern-Shell, solange eine Erweiterungsseite offen ist: sie meldet dann jede
     * Navigation, auch Zurueck/Vor, als `nodvard-deck:navigate` und `lattice:navigate`
     * (routes/ExtensionPage.tsx), und die Seite hoert nicht zusaetzlich auf `popstate`
     * (extensions/_shared/frontend/src/location.ts). */
    navigateEvents?: boolean;
    /** Zeitzone des Dashboards (z. B. "Europe/Berlin"), in der Zeitplaene ihre Uhrzeit meinen.
     * Setzt die Kern-Shell nach dem Anmelden (lib/deckTimezone.ts); aeltere Kerne kennen sie nicht. */
    timezone?: string | null;
  }
  interface Window {
    /** Neuer Name. Aeltere Kerne kennen ihn nicht -- dort ist er zur Laufzeit `undefined`. */
    __nodvardDeck: NodvardDeckShell;
    /** Alter Name, gilt weiter (Uebergang): dasselbe Objekt wie `__nodvardDeck`. */
    __lattice: NodvardDeckShell;
  }
}

export {};
