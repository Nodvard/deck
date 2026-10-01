import { ServerUnavailableError, useAuthStore } from "../state/auth";

/**
 * `window.__nodvardDeck.refreshAccessTokenResult()` fuer Extension-Seiten: erneuert das Login-Token
 * und sagt, wie es ausging (wirft nie):
 *
 * - `ok`: das neue Token.
 * - `rejected`: der Server lehnt die Anmeldung ab -- man ist wirklich abgemeldet, die Shell
 *   zeigt die Anmeldung.
 * - `unavailable`: der Server antwortet gerade nicht (Neustart nach einem Deploy, WLAN weg).
 *   Die Anmeldung bleibt, die Seite kann "Server gerade nicht erreichbar" zeigen statt
 *   "Nicht authentifiziert" (extensions/_shared/frontend/src/api.ts).
 *   Hat der Server geantwortet, aber mit einem echten Fehler (z. B. 507 "Speicherplatz voll",
 *   500), tragen `httpStatus` und `message` seinen Status und Grund -- `status` bleibt
 *   "unavailable", damit aeltere Seiten weiter "nicht erreichbar" zeigen. Ohne diese Felder
 *   (Netzwerkfehler, Zeitlimit, 502/503/504 ohne lesbaren Grund) gibt es keine Aussage des Servers.
 *
 * `refresh()` haengt sich an einen schon laufenden Aufruf an (state/auth.ts), parallele
 * Seiten rotieren das Cookie also nicht gegeneinander.
 */
export async function refreshAccessTokenResult(): Promise<NodvardDeckTokenRefreshResult> {
  let ok: boolean;
  try {
    ok = await useAuthStore.getState().refresh();
  } catch (err) {
    // refresh() wirft nur, wenn der Server nicht (sinnvoll) geantwortet hat (ServerUnavailableError);
    // `status` ist gesetzt, wenn er mit einem echten Fehler geantwortet hat.
    if (err instanceof ServerUnavailableError && err.status !== undefined) {
      return { status: "unavailable", httpStatus: err.status, message: err.message };
    }
    return { status: "unavailable" };
  }
  const token = ok ? useAuthStore.getState().accessToken : null;
  return token ? { status: "ok", token } : { status: "rejected" };
}

/**
 * `window.__nodvardDeck.refreshAccessToken()` fuer Extension-Seiten: erneuert das
 * Login-Token und liefert das neue -- oder `null`, wenn das nicht klappt (Refresh abgelehnt,
 * Server nicht erreichbar). Wirft nie; so steht es in der Schnittstelle, aeltere Bundles
 * verlassen sich darauf. Wer "abgelehnt" und "nicht erreichbar" unterscheiden will, nimmt
 * `refreshAccessTokenResult()`.
 */
export async function refreshAccessToken(): Promise<string | null> {
  const result = await refreshAccessTokenResult();
  return result.status === "ok" ? result.token : null;
}
