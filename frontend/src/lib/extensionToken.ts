import { ServerUnavailableError, useAuthStore } from "../state/auth";

/**
 * `window.__nodvardDeck.refreshAccessTokenResult()` für Extension-Seiten: erneuert das Token und sagt, wie es ausging
 * (wirft nie): `ok`, `rejected` (wirklich abgemeldet) oder `unavailable` (Server antwortet nicht, die Anmeldung
 * bleibt). `refresh()` knüpft an einen laufenden Aufruf an, parallele Seiten rotieren das Cookie nicht gegeneinander.
 */
export async function refreshAccessTokenResult(): Promise<NodvardDeckTokenRefreshResult> {
  let ok: boolean;
  try {
    ok = await useAuthStore.getState().refresh();
  } catch (err) {
    // refresh() wirft nur, wenn der Server nicht (sinnvoll) geantwortet hat. `status` ist gesetzt, wenn er mit einem
    // echten Fehler geantwortet hat; der Status bleibt "unavailable", damit ältere Seiten weiter "nicht erreichbar"
    // zeigen.
    if (err instanceof ServerUnavailableError && err.status !== undefined) {
      return { status: "unavailable", httpStatus: err.status, message: err.message };
    }
    return { status: "unavailable" };
  }
  const token = ok ? useAuthStore.getState().accessToken : null;
  return token ? { status: "ok", token } : { status: "rejected" };
}

/**
 * `window.__nodvardDeck.refreshAccessToken()` für Extension-Seiten: erneuert das Token und liefert das neue, sonst
 * `null` (Refresh abgelehnt, Server nicht erreichbar). Wirft nie, darauf verlassen sich ältere Bundles. Wer
 * "abgelehnt" und "nicht erreichbar" unterscheiden will, nimmt `refreshAccessTokenResult()`.
 */
export async function refreshAccessToken(): Promise<string | null> {
  const result = await refreshAccessTokenResult();
  return result.status === "ok" ? result.token : null;
}
