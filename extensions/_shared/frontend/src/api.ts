/**
 * Gemeinsame Aufrufe der Erweiterungsseiten gegen die Dashboard-API.
 *
 * Bisher hatte jede Seite ihr eigenes `authedFetch` -- alle nur mit dem aktuellen
 * Token, ohne zweiten Versuch. Nach 15 min Leerlauf (Hintergrund-Tab) kam beim
 * Speichern "Nicht authentifiziert". Jetzt holt sich der Aufruf bei 401 einmal ein
 * neues Token ueber den Kern (`window.__nodvardDeck.refreshAccessTokenResult()`, bei aelterem
 * Kern `refreshAccessToken()`) und wiederholt sich.
 *
 * Antwortet der Server gar nicht (Container startet nach einem Deploy neu, WLAN weg),
 * wirft der Aufruf `ServerUnavailableError` mit derselben Meldung wie der Kern
 * (frontend/src/lib/api.ts) -- bei einem echten Serverfehler beim Erneuern (z. B. 507) mit
 * dessen Grund ("Server meldet: ...") -- auch dann, wenn genau in diesem Moment das Token
 * ablaeuft: das Erneuern haengt am Kern (state/auth.ts: gemeinsamer Aufruf, Zeitlimit,
 * Wiederholen im Hintergrund), der meldet "Server antwortet nicht" getrennt von
 * "Anmeldung abgelehnt". Nur Letzteres kommt als 401 zurueck ("Nicht authentifiziert").
 *
 * Keine React-Hooks in dieser Datei (siehe AuthImage.tsx): so aendern sich nur die
 * Bundles, die sie wirklich einbinden.
 */
import { deck } from "./deck";

/** Wie `SERVER_UNAVAILABLE_TEXT` im Kern (frontend/src/state/auth.ts). */
export const SERVER_UNAVAILABLE_TEXT = "Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.";

/**
 * `authedFetch` wirft das, wenn keine brauchbare Antwort kommt: Netzwerkfehler, 502/503/504
 * ohne eigenen Grund (Proxy, solange der Server nicht laeuft) oder der Kern kann das Token
 * gerade nicht erneuern. Die Anmeldung bleibt; spaeter noch einmal versuchen hilft.
 *
 * Hat der Server beim Erneuern mit einem echten Fehler geantwortet (z. B. 507 "Speicherplatz
 * voll"), sind `status` und `message` ("Server meldet: <Grund>") gesetzt -- wie im Kern
 * (frontend/src/routes/RequireAuth.tsx).
 */
export class ServerUnavailableError extends Error {
  status?: number;
  constructor(answer?: { status: number; message: string }) {
    super(answer ? `Server meldet: ${answer.message}` : SERVER_UNAVAILABLE_TEXT);
    this.name = "ServerUnavailableError";
    this.status = answer?.status;
  }
}

/** Antworten, die ein Proxy schickt, solange der Server nicht (fertig) laeuft. */
const SERVER_DOWN_STATUS = new Set([502, 503, 504]);

/** Request gegen `/api/v1<path>` mit Login-Token; bei 401 einmal Token erneuern und wiederholen. */
export async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = async (token: string | null): Promise<Response> => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    // JSON-Body ohne eigenen Typ; Uploads (Blob) setzen ihren Content-Type selbst.
    if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    let res: Response;
    try {
      res = await fetch(`/api/v1${path}`, { ...init, headers });
    } catch (err) {
      // fetch wirft einen TypeError, wenn gar keine Antwort kommt. Ein AbortError (Seite
      // verlassen, Logs geschlossen) bleibt, wie er ist.
      if (err instanceof TypeError) throw new ServerUnavailableError();
      throw err;
    }
    // Eigene 502 des Backends ("Nicht erreichbar: timeout" vom Zielserver) tragen einen Grund
    // und bleiben normale Antworten; ohne Grund kommt die Seite vom Proxy.
    if (SERVER_DOWN_STATUS.has(res.status) && bodyDetail(await res.clone().json().catch(() => null)) === null) {
      throw new ServerUnavailableError();
    }
    return res;
  };

  const shell = deck();
  const sent = shell.getAccessToken();
  const res = await send(sent);
  if (res.status !== 401 || (!shell.refreshAccessTokenResult && !shell.refreshAccessToken)) return res;

  // Ein anderer Aufruf war evtl. schneller und hat das Token schon erneuert -- dann
  // reicht der zweite Versuch damit. Sonst erneuern (haengt sich an einen laufenden
  // Refresh an, siehe state/auth.ts).
  let token = shell.getAccessToken();
  if (!token || token === sent) token = await renewToken();
  // null: der Server hat die Anmeldung abgelehnt -- dann bleibt es beim 401.
  if (!token) return res;
  return send(token);
}

/**
 * Neues Token vom Kern; `null`, wenn der Server die Anmeldung ablehnt. Antwortet er beim
 * Erneuern nicht, ist das kein "abgemeldet" -- dann `ServerUnavailableError`, die Shell
 * versucht es selbst weiter (lib/tokenRefresh.ts).
 */
async function renewToken(): Promise<string | null> {
  const shell = deck();
  if (shell.refreshAccessTokenResult) {
    const result = await shell.refreshAccessTokenResult().catch((): NodvardDeckTokenRefreshResult => ({ status: "unavailable" }));
    if (result.status === "unavailable") {
      // Aeltere Kerne kennen httpStatus/message nicht -- dann wie bisher "nicht erreichbar".
      const reason = typeof result.message === "string" && result.message.trim() !== "" ? result.message : undefined;
      if (typeof result.httpStatus === "number" && reason !== undefined) {
        throw new ServerUnavailableError({ status: result.httpStatus, message: reason });
      }
      throw new ServerUnavailableError();
    }
    return result.status === "ok" ? result.token : null;
  }
  // Aelterer Kern: kennt nur "Token oder null", null heisst dort auch "Server antwortet nicht".
  return shell.refreshAccessToken ? shell.refreshAccessToken().catch(() => null) : null;
}

/** Lesbarer Grund aus dem Body einer Fehlerantwort, sonst `null`. */
function bodyDetail(body: unknown): string | null {
  const b = (typeof body === "object" && body !== null ? body : {}) as {
    detail?: unknown;
    gate_decision?: { detail?: unknown } | null;
  };
  if (typeof b.detail === "string" && b.detail) return b.detail;
  if (Array.isArray(b.detail)) {
    const parts = b.detail
      .map((item) => (typeof item === "string" ? item : (item as { msg?: unknown } | null)?.msg))
      .filter((part): part is string => typeof part === "string" && part !== "");
    if (parts.length > 0) return parts.join("; ");
  }
  const gate = b.gate_decision?.detail;
  if (typeof gate === "string" && gate) return gate;
  return null;
}

/** Meldung aus dem Body einer Fehlerantwort: `detail` (Text oder Liste aus FastAPI-
 * Pruefung), sonst der Grund der Gate-Entscheidung, sonst "HTTP <status>" -- bei
 * 502/503/504 ohne Grund dieselbe Meldung wie bei fehlender Verbindung. */
export function errorFromBody(body: unknown, status: number): string {
  return bodyDetail(body) ?? (SERVER_DOWN_STATUS.has(status) ? SERVER_UNAVAILABLE_TEXT : `HTTP ${status}`);
}

/** Fehlertext einer nicht erfolgreichen Antwort (liest den Body, nie eine Ausnahme). */
export async function errorText(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return errorFromBody(body, res.status);
}
