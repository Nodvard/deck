import { useEffect, useState } from "react";
import { Link, Navigate, Outlet, useLocation } from "react-router-dom";

import { retryDelayMs } from "../lib/tokenRefresh";
import { ServerUnavailableError, useAuthStore } from "../state/auth";

/**
 * Adresse, zu der die Anmeldung zurückführen soll. Seiten wie Nodvard Shield schreiben Reiter und Filter per
 * replaceState in die Adresszeile (`_shared/location.ts`), am Router vorbei; dessen `location` kennt nur die letzte
 * echte Navigation. Bei derselben Seite gilt daher die Adresszeile, sonst (z. B. in Tests) der Router-Stand.
 */
function returnAddress(location: { pathname: string; search: string; hash: string }): string {
  const { pathname, search, hash } = window.location;
  return pathname === location.pathname ? pathname + search + hash : location.pathname + location.search + location.hash;
}

/**
 * Ein Reload hält keinen Access-Token im Speicher: Bei "unknown" versucht die Seite einen stillen Refresh über das
 * Cookie, bevor sie zum Login umleitet. Antwortet der Server nicht oder mit einem Fehler, bleibt sie mit Hinweis und
 * neuen Versuchen stehen; bei einem dauerhaften Fehler helfen "Jetzt erneut versuchen" und der Link zur Anmeldung.
 */
export function RequireAuth() {
  const status = useAuthStore((s) => s.status);
  const refresh = useAuthStore((s) => s.refresh);
  const location = useLocation();
  const [unavailable, setUnavailable] = useState(false);
  /** Grund, den der Server bei einem echten Fehler (507, 500, ...) genannt hat; `null` = nicht erreichbar. */
  const [serverText, setServerText] = useState<string | null>(null);
  /** Ein Versuch läuft gerade (sperrt den Knopf). */
  const [trying, setTrying] = useState(false);
  /** Zählt die Klicks auf "Jetzt erneut versuchen": ein neuer Wert startet den Effekt neu. */
  const [manualTries, setManualTries] = useState(0);

  useEffect(() => {
    if (status !== "unknown") return;
    let active = true;
    let timer: number | undefined;
    let failures = 0;

    const attempt = () => {
      setTrying(true);
      // Bei Erfolg oder Ablehnung ändert refresh() den Status, der Effekt endet dann.
      refresh().then(
        () => {
          if (!active) return;
          setUnavailable(false);
          setServerText(null);
          setTrying(false);
        },
        (err: unknown) => {
          if (!active) return;
          setUnavailable(true);
          setServerText(err instanceof ServerUnavailableError && err.status !== undefined ? err.message : null);
          setTrying(false);
          timer = window.setTimeout(attempt, retryDelayMs(failures));
          failures += 1;
        },
      );
    };
    attempt();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [status, refresh, manualTries]);

  // Der Hinweis erscheint nur, wenn `index.html` da ist, die API aber nicht antwortet (Seite aus dem Cache, Ausfall
  // kurz nach dem Laden, Server mit Fehlermeldung). Bei gestopptem Container zeigt der Browser seine eigene Seite.
  if (status === "unknown") {
    return unavailable ? (
      <div className="p-6 text-sm">
        {serverText === null ? (
          <p role="status" className="opacity-60">Server nicht erreichbar – neuer Versuch …</p>
        ) : (
          <>
            <p role="status">Server meldet: {serverText}</p>
            <p className="mt-1 text-xs opacity-60">Neuer Versuch folgt automatisch …</p>
          </>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={trying}
            onClick={() => setManualTries((n) => n + 1)}
            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-50"
          >
            {trying ? "Versuche es …" : "Jetzt erneut versuchen"}
          </button>
          <Link to="/login" state={{ from: returnAddress(location) }} className="text-xs underline opacity-80 hover:opacity-100">
            Zur Anmeldung
          </Link>
        </div>
      </div>
    ) : (
      <p className="p-6 text-sm opacity-60">Lade …</p>
    );
  }
  // Mit Abfrage und Anker: ntfy-Links wie /ext/shield/soc?tab=guard&host=... sollen nach der Anmeldung im richtigen
  // Reiter landen.
  if (status !== "authenticated") {
    return <Navigate to="/login" state={{ from: returnAddress(location) }} replace />;
  }
  return <Outlet />;
}
