// Ambient global für den Import-Map-Shim (docs/02-EXTENSION-API.md, window.__nodvardDeck).
// Eigene Datei statt eines `declare global` in main.tsx, damit extensions/tsconfig.json dieselbe Deklaration einbinden
// kann, ohne main.tsx als Modul mitzuziehen (die Extension-Frontends importieren main.tsx nie).
import type * as React from "react";
import type * as ReactDOM from "react-dom";
import type * as ReactDOMClient from "react-dom/client";
import type * as ReactJsxRuntime from "react/jsx-runtime";

declare global {
  /** Build-Kennung (vite.config.ts `define`); in Tests nicht gesetzt. */
  const __BUILD_ID__: string;
  /** Ergebnis von `window.__nodvardDeck.refreshAccessTokenResult()` (lib/extensionToken.ts). */
  type NodvardDeckTokenRefreshResult =
    | { status: "ok"; token: string }
    /** Der Server lehnt die Anmeldung ab: wirklich abgemeldet. */
    | { status: "rejected" }
    /** Der Server antwortet gerade nicht (Neustart, WLAN weg) oder mit einem Fehler: die Anmeldung bleibt.
     * `httpStatus`/`message` (Status und Grund des Servers, z. B. 507 "Speicherplatz voll") gibt es nur, wenn er
     * wirklich geantwortet hat; ältere Kerne kennen sie nicht. */
    | { status: "unavailable"; httpStatus?: number; message?: string };
  /** Das globale Objekt der Kern-Shell (main.tsx) am Fenster; Erweiterungscode holt es über `deck()` bzw. `findDeck()`
   * (lib/deckGlobal.ts). */
  interface NodvardDeckShell {
    React: typeof React;
    ReactDOM: typeof ReactDOM & typeof ReactDOMClient;
    ReactJsxRuntime: typeof ReactJsxRuntime;
    getAccessToken: () => string | null;
    /** Holt ein neues Token (Refresh-Cookie) und liefert es, bei Fehlschlag null (wirft nie). Optional, damit
     * Test-Attrappen nicht alle nachziehen müssen, die Kern-Shell setzt es immer. */
    refreshAccessToken?: () => Promise<string | null>;
    /** Wie `refreshAccessToken`, sagt aber, warum es nicht geklappt hat: abgelehnt oder Server gerade nicht erreichbar
     * (wirft nie). Optional: ältere Kerne kennen es nicht. */
    refreshAccessTokenResult?: () => Promise<NodvardDeckTokenRefreshResult>;
    confirmDialog: (message: string, options?: { title?: string; danger?: boolean; confirmLabel?: string; cancelLabel?: string }) => Promise<boolean>;
    promptDialog: (message: string, defaultValue?: string) => Promise<string | null>;
    /** Fragt das eigene Passwort und, mit `twoFactor`, den Code aus der App ab (`null` bei Abbruch), etwa wenn der Kern
     * mit 403 `confirm_required` antwortet. Optional: ältere Kerne kennen es nicht. */
    confirmIdentity?: (message: string, twoFactor?: boolean) => Promise<{ current_password: string; totp_code?: string } | null>;
    hasPermission: (permission: string) => boolean;
    /** Setzt die Kern-Shell, solange eine Erweiterungsseite offen ist: Sie meldet dann jede Navigation, auch
     * Zurück/Vor, als `nodvard-deck:navigate` (routes/ExtensionPage.tsx), und die Seite hört
     * nicht zusätzlich auf `popstate` (extensions/_shared/frontend/src/location.ts). */
    navigateEvents?: boolean;
    /** Zeitzone des Dashboards (z. B. "Europe/Berlin"), in der Zeitpläne ihre Uhrzeit meinen. Setzt die Kern-Shell
     * nach dem Anmelden (lib/deckTimezone.ts); ältere Kerne kennen sie nicht. */
    timezone?: string | null;
  }
  interface Window {
    __nodvardDeck: NodvardDeckShell;
  }
}

export {};
