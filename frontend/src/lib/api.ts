/**
 * Zentraler Fetch-Wrapper: hängt den Access-Token an, versucht bei einem 401 genau einmal einen stillen Refresh über
 * das Cookie und wiederholt die Anfrage (kein Retry-Loop). Lehnt der Server ab, ist man abgemeldet; antwortet er nicht,
 * bleibt die Anmeldung. Ohne JSON-Antwort: `apiFetchResponse`; für Uploads: `refreshAfterUnauthorized`, `apiErrorFromBody`.
 */
import { SERVER_UNAVAILABLE_TEXT, ServerUnavailableError, useAuthStore } from "../state/auth";
import { validationText } from "./validation";

export class ApiError extends Error {
  status: number;
  /** Der ganze geparste Fehler-Body, nicht nur `detail` (z. B. die ActionOut einer Gate-Sperre). */
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
    // `fetch` wirft einen TypeError, wenn keine Antwort kommt (Neustart, WLAN weg): statt "Failed to fetch" die
    // verständliche Meldung, als 503, damit Abfrage-Schleifen wie bei jedem 5xx weiterfragen. Ein AbortError
    // (Seite verlassen, lib/actions.ts) bleibt unverändert.
    if (err instanceof TypeError) throw new ApiError(503, SERVER_UNAVAILABLE_TEXT);
    throw err;
  }
}

/** Antworten, die ein Proxy schickt, solange der Server nicht (fertig) läuft. */
const SERVER_DOWN_STATUS = new Set([502, 503, 504]);

function parseErrorBody(text: string): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * Lesbare Meldung aus dem Fehler-Body: `detail` allein reicht nicht, eine Gate-Sperre (403 ohne `detail`) würde zu
 * "HTTP 403", eine 422-Liste in `detail` zu "[object Object]".
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
 * Der Fehler zu einer abgelehnten Antwort aus Status und Text (für XMLHttpRequest): Text aus `detail`, sonst
 * "HTTP <Status>". 502/503/504 ohne lesbaren Text (Fehlerseite vom Proxy) heißen wie bei fehlender Verbindung.
 */
export function apiErrorFromBody(status: number, text: string): ApiError {
  const detail = parseErrorBody(text);
  const fallback = SERVER_DOWN_STATUS.has(status) ? SERVER_UNAVAILABLE_TEXT : `HTTP ${status}`;
  return new ApiError(status, errorMessage(detail) ?? fallback, detail);
}

/** Wie `apiErrorFromBody`, liest den Text aus der Antwort. */
export async function apiErrorFromResponse(res: Response): Promise<ApiError> {
  return apiErrorFromBody(res.status, await res.text().catch(() => ""));
}

/**
 * Erneuert die Anmeldung still über das Cookie (zweiter Versuch nach 401): `true` = neues Token im Store, `false` =
 * Server lehnt ab (abgemeldet). Sonst ein ApiError mit Status und Text des Servers (z. B. 507), ohne Antwort 503,
 * damit Aufrufer `err.message` zeigen und Abfrage-Schleifen wie bei jedem 5xx weiterfragen.
 */
export async function refreshAfterUnauthorized(): Promise<boolean> {
  try {
    return await useAuthStore.getState().refresh();
  } catch (err) {
    if (err instanceof ServerUnavailableError) {
      throw err.status !== undefined ? new ApiError(err.status, err.message, err.detail) : new ApiError(503, err.message);
    }
    throw err;
  }
}

/**
 * Die rohe Antwort (auch bei einem Fehlerstatus) mit derselben Erneuerung bei 401 wie `apiFetch`. `skipAuthRetry` und
 * der Pfad `/auth/refresh` verhindern eine Endlosschleife. Den Fehler bei Status >= 400 baut `apiErrorFromResponse`.
 */
export async function apiFetchResponse(
  path: string,
  init: RequestInit = {},
  skipAuthRetry = false,
): Promise<Response> {
  let res = await doFetch(path, init);
  if (res.status === 401 && !skipAuthRetry && path !== "/auth/refresh") {
    if (await refreshAfterUnauthorized()) {
      res = await doFetch(path, init);
    }
  }
  return res;
}

/** `skipAuthRetry` verhindert eine Endlosschleife, wenn der stille Refresh selbst 401 liefert (abgelaufenes Cookie). */
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  skipAuthRetry = false,
): Promise<T> {
  const res = await apiFetchResponse(path, init, skipAuthRetry);

  if (!res.ok) throw await apiErrorFromResponse(res);

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
