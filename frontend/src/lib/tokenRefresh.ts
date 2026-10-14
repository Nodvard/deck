/**
 * Erneuert den Access-Token kurz vor Ablauf, nur im sichtbaren Tab: Der Server rotiert das Refresh-Token bei jeder
 * Einlösung, ein Takt in jedem Tab ließe Tabs gegeneinander laufen. Verpasst ein Tab den Takt (Hintergrund, Standby),
 * erneuern die Aufrufe bei einem 401 selbst (lib/api.ts, lib/extensionToken.ts, lib/restore.ts).
 */
import { useEffect } from "react";

import { useAuthStore } from "../state/auth";

/** So lange vor Ablauf wird erneuert. */
export const REFRESH_MARGIN_MS = 2 * 60_000;
/** Wartezeiten bis zum nächsten Versuch, wenn der Server gerade nicht antwortet (z. B. Neustart nach Deploy). */
const RETRY_DELAYS_MS = [5_000, 15_000, 30_000];
/** Danach alle 60 s, solange der Tab sichtbar ist. */
const RETRY_REPEAT_MS = 60_000;
/** Standard-Laufzeit des Backends, falls das Token nicht lesbar ist. */
const FALLBACK_LIFETIME_MS = 15 * 60_000;

/**
 * Laufzeit des Tokens aus `exp - iat` des JWT. Bewusst relativ statt `exp` mit der Browser-Uhr zu vergleichen: Geht die
 * Uhr des Geräts falsch, läge `exp` sonst schon "in der Vergangenheit" und es gäbe eine Refresh-Schleife.
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

/** Wann nach Erhalt erneuert wird: 2 min vor Ablauf, bei sehr kurzen Tokens zur Hälfte der Laufzeit. */
export function refreshAfterMs(lifetimeMs: number): number {
  return Math.max(lifetimeMs - REFRESH_MARGIN_MS, lifetimeMs / 2);
}

/**
 * Wartezeit vor dem Versuch Nr. `failures + 1` nach `failures` gescheiterten Versuchen: 5 s, 15 s, 30 s, dann alle
 * 60 s. Auch `RequireAuth` nutzt das beim ersten Laden der Seite.
 */
export function retryDelayMs(failures: number): number {
  return RETRY_DELAYS_MS[failures] ?? RETRY_REPEAT_MS;
}

/** In AppShell eingehängt, gilt damit für jede Seite hinter dem Login. */
export function useProactiveTokenRefresh() {
  const accessToken = useAuthStore((s) => s.accessToken);

  useEffect(() => {
    if (!accessToken) return;
    // Zeitpunkt des Erhalts: Der Effekt läuft, sobald ein neues Token im Store liegt.
    const dueAt = Date.now() + refreshAfterMs(tokenLifetimeMs(accessToken));
    let timer: number | undefined;
    let active = true;
    /** Gescheiterte Versuche in Folge (Server antwortet nicht), bestimmt die Wartezeit. */
    let failures = 0;

    const refreshIfVisible = () => {
      // Ein noch wartender Wiederholungs-Timer würde sonst neben diesem Aufruf laufen.
      window.clearTimeout(timer);
      if (document.visibilityState !== "visible") return;
      // refresh() hängt sich an einen laufenden Aufruf an (state/auth.ts). Bei Erfolg plant der Effekt neu, bei
      // Ablehnung (false) endet er. Antwortet der Server nicht, bleibt die Anmeldung bestehen und es wird mit längeren
      // Pausen weiterversucht. Ein Hintergrund-Tab wartet, bis er wieder angezeigt wird (onVisibilityChange).
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
