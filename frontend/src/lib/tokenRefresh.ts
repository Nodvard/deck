/**
 * Access-Token vorab erneuern.
 *
 * Bei einem 401 erneuern auch die Aufrufe selbst, einmal still, dann folgt ein zweiter
 * Versuch: `apiFetch` und `apiFetchResponse` (Datei laden, Protokoll-Export), der Upload im
 * Dateimanager (routes/FilesPage.tsx) und der Upload einer Sicherung (lib/restore.ts), alle
 * ueber `refreshAfterUnauthorized` aus lib/api.ts, dazu das gemeinsame `authedFetch` der
 * Extensions (UI-Kit, api.ts, ueber `refreshAccessTokenResult` aus lib/extensionToken.ts). Das ist die
 * Absicherung, nicht der Takt: Nach 15 min (config.py access_token_ttl_seconds) laeuft das
 * Token ab, deshalb holt die Shell das neue selbst, kurz BEVOR das alte ablaeuft, und es
 * gibt im Normalfall gar keinen 401. Ein Tab im Hintergrund oder ein Rechner im
 * Ruhezustand verpasst diesen Takt -- dort greift der zweite Versuch.
 *
 * Nur im SICHTBAREN Tab: services/auth.py rotiert das Refresh-Token bei jeder
 * Einloesung ohne Gnadenfrist -- ein fester Takt in jedem offenen Tab liesse zwei
 * Tabs gegeneinander laufen, einer davon wuerde abgemeldet. Ein Hintergrund-Tab holt
 * das Token deshalb erst, wenn er wieder angezeigt wird.
 *
 * Antwortet der Server nicht (Netzwerkfehler, 5xx -- z. B. waehrend der Container nach
 * einem Deploy neu startet), bleibt der Nutzer angemeldet und der Versuch wird mit
 * laenger werdenden Pausen wiederholt. Nur wenn der Server das Refresh-Cookie ablehnt
 * (400/401/403), ist die Anmeldung vorbei (state/auth.ts).
 */
import { useEffect } from "react";

import { useAuthStore } from "../state/auth";

/** So lange vor Ablauf wird erneuert. */
export const REFRESH_MARGIN_MS = 2 * 60_000;
/** Wartezeiten bis zum naechsten Versuch, wenn der Server gerade nicht antwortet (z. B. Neustart nach Deploy). */
const RETRY_DELAYS_MS = [5_000, 15_000, 30_000];
/** Danach alle 60 s, solange der Tab sichtbar ist. */
const RETRY_REPEAT_MS = 60_000;
/** Standard-Laufzeit des Backends, falls das Token nicht lesbar ist. */
const FALLBACK_LIFETIME_MS = 15 * 60_000;

/**
 * Laufzeit des Tokens aus `exp - iat` des JWT (core/security.py::create_jwt). Bewusst
 * relativ statt `exp` mit der Browser-Uhr zu vergleichen: geht die Uhr des Geraets
 * falsch, laege `exp` sonst schon "in der Vergangenheit" und es gaebe eine
 * Refresh-Schleife.
 */
export function tokenLifetimeMs(token: string): number {
  try {
    const part = token.split(".")[1] ?? "";
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const { exp, iat } = JSON.parse(atob(base64)) as { exp?: unknown; iat?: unknown };
    if (typeof exp === "number" && typeof iat === "number" && exp > iat) return (exp - iat) * 1000;
  } catch {
    // kein lesbares JWT -> Standard-Laufzeit
  }
  return FALLBACK_LIFETIME_MS;
}

/** Wann nach Erhalt erneuert wird: 2 min vor Ablauf, bei sehr kurzen Tokens zur Haelfte der Laufzeit. */
export function refreshAfterMs(lifetimeMs: number): number {
  return Math.max(lifetimeMs - REFRESH_MARGIN_MS, lifetimeMs / 2);
}

/**
 * Wartezeit vor dem Versuch Nr. `failures + 1`, nachdem `failures` Versuche in Folge
 * gescheitert sind: 5 s, 15 s, 30 s, dann alle 60 s. Auch `RequireAuth` nutzt das beim
 * ersten Laden der Seite.
 */
export function retryDelayMs(failures: number): number {
  return RETRY_DELAYS_MS[failures] ?? RETRY_REPEAT_MS;
}

/** In AppShell eingehaengt -- gilt damit fuer jede Seite hinter dem Login. */
export function useProactiveTokenRefresh() {
  const accessToken = useAuthStore((s) => s.accessToken);

  useEffect(() => {
    if (!accessToken) return;
    // Zeitpunkt des Erhalts: der Effekt laeuft, sobald ein neues Token im Store liegt.
    const dueAt = Date.now() + refreshAfterMs(tokenLifetimeMs(accessToken));
    let timer: number | undefined;
    let active = true;
    /** Gescheiterte Versuche in Folge (Server antwortet nicht) -- bestimmt die Wartezeit. */
    let failures = 0;

    const refreshIfVisible = () => {
      // Ein noch wartender Wiederholungs-Timer wuerde sonst neben diesem Aufruf laufen.
      window.clearTimeout(timer);
      if (document.visibilityState !== "visible") return;
      // refresh() haengt sich an einen schon laufenden Aufruf an (state/auth.ts). Bei
      // Erfolg liegt ein neues Token im Store, der Effekt plant dann neu; lehnt der
      // Server ab (false), ist das Token weg und der Effekt endet. Antwortet der
      // Server nicht, bleibt die Anmeldung bestehen -- mit laenger werdenden Pausen
      // (5 s, 15 s, 30 s, dann jede Minute) wird es weiter versucht. Ein Hintergrund-
      // Tab wartet, bis er wieder angezeigt wird (onVisibilityChange).
      useAuthStore.getState().refresh().catch(() => {
        if (!active) return;
        window.clearTimeout(timer);
        timer = window.setTimeout(refreshIfVisible, retryDelayMs(failures));
        failures += 1;
      });
    };
    const onVisibilityChange = () => {
      if (Date.now() >= dueAt) refreshIfVisible();
    };

    timer = window.setTimeout(refreshIfVisible, dueAt - Date.now());
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [accessToken]);
}
