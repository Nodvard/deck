/**
 * Zentraler Fetch-Wrapper -- haengt den Access-Token an, und wenn eine Anfrage mit
 * 401 zurueckkommt, wird GENAU EINMAL ein stiller Refresh versucht (ueber das
 * HttpOnly-Cookie, siehe state/auth.ts) und die Anfrage wiederholt. Lehnt der Server
 * den Refresh ab, wird der Nutzer als abgemeldet markiert -- kein Retry-Loop. Antwortet
 * der Server gar nicht (Neustart nach einem Deploy), bleibt die Anmeldung bestehen und
 * nur diese Anfrage schlaegt mit einer verstaendlichen Meldung fehl.
 */
import { SERVER_UNAVAILABLE_TEXT, ServerUnavailableError, useAuthStore } from "../state/auth";

export class ApiError extends Error {
  status: number;
  /** Der komplette, geparste Fehler-Body (nicht nur sein Feld `detail`) -- z. B. die
   * ActionOut einer Gate-Sperre, siehe lib/actionOutcome.ts. */
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function doFetch(path: string, init: RequestInit): Promise<Response> {
  const token = useAuthStore.getState().accessToken;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  try {
    return await fetch(`/api/v1${path}`, { ...init, headers });
  } catch (err) {
    // `fetch` wirft einen TypeError, wenn gar keine Antwort kommt (Server neu gestartet,
    // WLAN weg) -- statt "Failed to fetch" die verstaendliche Meldung. Ein AbortError
    // (Seite verlassen, siehe lib/actions.ts) bleibt unveraendert. Als 503, damit
    // Abfrage-Schleifen wie bei jedem 5xx einfach weiterfragen.
    if (err instanceof TypeError) throw new ApiError(503, SERVER_UNAVAILABLE_TEXT);
    throw err;
  }
}

/** Antworten, die ein Proxy schickt, solange der Server nicht (fertig) laeuft. */
const SERVER_DOWN_STATUS = new Set([502, 503, 504]);

async function readErrorDetail(res: Response): Promise<unknown> {
  const text = await res.text().catch(() => "");
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** FastAPI-Validierungsfehler: `[{loc: ["body", "payload", "snapname"], msg}, ...]`. */
function validationText(items: unknown[]): string {
  return items
    .map((item) => {
      if (typeof item !== "object" || item === null) return String(item);
      const { loc, msg } = item as { loc?: unknown; msg?: unknown };
      const where = Array.isArray(loc) ? loc.filter((part) => !["body", "query", "path"].includes(String(part))).join(".") : "";
      const text = typeof msg === "string" ? msg : JSON.stringify(item);
      return where ? `${where}: ${text}` : text;
    })
    .join("; ");
}

/**
 * Lesbare Meldung aus dem Fehler-Body: nur `detail` reicht nicht -- eine Gate-Sperre
 * (403 mit ActionOut, ohne `detail`) wuerde so zu "HTTP 403", eine 422 mit einer
 * Liste in `detail` zu "[object Object]".
 */
function errorMessage(body: unknown): string | undefined {
  if (typeof body !== "object" || body === null) return undefined;
  const { detail, gate_decision: gate } = body as { detail?: unknown; gate_decision?: unknown };
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) return validationText(detail);
  if (typeof detail === "number" || typeof detail === "boolean") return String(detail);
  if (typeof gate === "object" && gate !== null) {
    const gateDetail = (gate as { detail?: unknown }).detail;
    if (typeof gateDetail === "string" && gateDetail.trim()) return gateDetail;
  }
  return undefined;
}

/**
 * `skipAuthRetry` verhindert eine Endlosschleife, wenn der stille Refresh selbst
 * 401 liefert (abgelaufener/fehlender Refresh-Cookie).
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  skipAuthRetry = false,
): Promise<T> {
  let res = await doFetch(path, init);

  if (res.status === 401 && !skipAuthRetry && path !== "/auth/refresh") {
    let refreshed: boolean;
    try {
      refreshed = await useAuthStore.getState().refresh();
    } catch (err) {
      // Als ApiError, damit Aufrufer `err.message` zeigen koennen und Abfrage-Schleifen
      // (lib/actions.ts) wie bei jedem 5xx einfach weiterfragen. Hat der Server geantwortet
      // (z. B. 507 "Speicherplatz voll"), gilt sein Status und Text; sonst 503 "nicht erreichbar".
      if (err instanceof ServerUnavailableError) {
        throw err.status !== undefined ? new ApiError(err.status, err.message, err.detail) : new ApiError(503, err.message);
      }
      throw err;
    }
    if (refreshed) {
      res = await doFetch(path, init);
    }
  }

  if (!res.ok) {
    const detail = await readErrorDetail(res);
    // 502/503/504 kommen meist als HTML vom Proxy, ohne lesbaren Text: dann dieselbe
    // Meldung wie bei fehlender Verbindung statt "HTTP 502".
    const fallback = SERVER_DOWN_STATUS.has(res.status) ? SERVER_UNAVAILABLE_TEXT : `HTTP ${res.status}`;
    throw new ApiError(res.status, errorMessage(detail) ?? fallback, detail);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: "DELETE", body: body !== undefined ? JSON.stringify(body) : undefined }),
};
