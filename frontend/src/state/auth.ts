/**
 * Auth-Zustand. Der Access-Token lebt nur im Speicher, nie in localStorage: Ein XSS-Angriff, der localStorage liest,
 * könnte sonst einen 30 Tage gültigen Zugang stehlen statt nur einen 15-Minuten-Token. Der Refresh-Token ist ein
 * HttpOnly-Cookie; `apiFetch` (lib/api.ts) liest den Zustand außerhalb von React über `useAuthStore.getState()`.
 */
import { create } from "zustand";

import { AUTH_FIELD_LABELS, validationText } from "../lib/validation";

export interface CurrentUser {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  is_owner: boolean;
  locale: string;
  permissions: string[];
}

interface AuthState {
  accessToken: string | null;
  user: CurrentUser | null;
  status: "unknown" | "authenticated" | "anonymous";
  mfaToken: string | null;

  login: (username: string, password: string) => Promise<{ ok: true } | { ok: false; mfaToken: string } | { ok: false; error: string }>;
  bootstrap: (username: string, password: string, setupCode: string) => Promise<{ ok: true } | { ok: false; error: string }>;
  submitMfa: (code: string) => Promise<{ ok: true } | { ok: false; error: string }>;
  /** true = erneuert, false = Server lehnt ab (abgemeldet); wirft `ServerUnavailableError`, wenn er nicht antwortet. */
  refresh: () => Promise<boolean>;
  logout: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
}

function matchesPermission(granted: string[], required: string): boolean {
  const [reqBase, reqScope] = required.split(":", 2) as [string, string | undefined];
  for (const g of granted) {
    const [gBase, gScope] = g.split(":", 2) as [string, string | undefined];
    if (gBase === "*") return true;
    if (gBase !== reqBase) continue;
    if (gScope === undefined) return true;
    if (reqScope === undefined) return false;
    if (gScope === "*" || gScope.endsWith("*")) {
      if (gScope === "*" || reqScope.startsWith(gScope.slice(0, -1))) return true;
      continue;
    }
    if (gScope === reqScope) return true;
  }
  return false;
}

async function parseJsonResponse<T>(res: Response): Promise<T> {
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

/**
 * Fehlertext einer abgelehnten Auth-Antwort. Die 422-Liste wird zu einem Satz zusammengesetzt, weil sie als
 * React-Kind gerendert die Anmeldeseite abstürzen ließe.
 */
export async function authErrorText(res: Response): Promise<string> {
  const body = await parseJsonResponse<{ detail?: unknown }>(res).catch(() => ({}) as { detail?: unknown });
  if (typeof body.detail === "string" && body.detail) return body.detail;
  // 422: die Liste der abgelehnten Felder ("Passwort: Mindestens 8 Zeichen.") statt "HTTP 422".
  if (Array.isArray(body.detail) && body.detail.length > 0) return validationText(body.detail, AUTH_FIELD_LABELS);
  if (res.status === 429) {
    const seconds = Number(res.headers.get("Retry-After"));
    if (Number.isFinite(seconds) && seconds > 0) {
      const minutes = Math.max(1, Math.ceil(seconds / 60));
      return `Zu viele Fehlversuche. Bitte in ${minutes} ${minutes === 1 ? "Minute" : "Minuten"} erneut versuchen.`;
    }
    return "Zu viele Fehlversuche. Bitte später erneut versuchen.";
  }
  return `HTTP ${res.status}`;
}

/** Meldung, wenn der Server beim Erneuern der Anmeldung gerade nicht antwortet. */
export const SERVER_UNAVAILABLE_TEXT = "Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.";

/**
 * `refresh()` wirft das, wenn die Erneuerung scheitert, das Cookie aber nicht abgelehnt wurde (Netzwerk, Zeitlimit,
 * 5xx, unlesbare Antwort). Der Store bleibt dann unverändert, nur `false` heißt abgemeldet. Mit `status` nennt der
 * Server einen Grund (`message`, `detail` = Body), ohne `status` ist er nicht erreichbar.
 */
export class ServerUnavailableError extends Error {
  status?: number;
  detail?: unknown;
  constructor(answer?: { status: number; message: string; detail?: unknown }) {
    super(answer?.message ?? SERVER_UNAVAILABLE_TEXT);
    this.name = "ServerUnavailableError";
    this.status = answer?.status;
    this.detail = answer?.detail;
  }
}

/** Antworten, die ein Proxy schickt, solange der Server nicht (fertig) läuft. */
const SERVER_DOWN_STATUS = new Set([502, 503, 504]);

/**
 * Fehler zu einer /auth/refresh-Antwort ohne Erfolg und ohne Ablehnung; 502/503/504 ohne lesbaren Grund
 * (HTML vom Proxy) heißen weiter "nicht erreichbar", alles andere trägt Status und Grund des Servers.
 */
async function refreshFailure(res: Response): Promise<ServerUnavailableError> {
  const text = await res.text().catch(() => "");
  let detail: unknown = text || undefined;
  try {
    if (text) detail = JSON.parse(text);
  } catch {
    /* kein JSON: der rohe Text bleibt `detail` */
  }
  const reason = typeof detail === "object" && detail !== null ? (detail as { detail?: unknown }).detail : undefined;
  const readable = typeof reason === "string" && reason.trim() !== "" ? reason : undefined;
  if (SERVER_DOWN_STATUS.has(res.status) && readable === undefined) return new ServerUnavailableError();
  return new ServerUnavailableError({ status: res.status, message: readable ?? `HTTP ${res.status}`, detail });
}

/**
 * So lange wartet `refresh()` höchstens auf die Antwort: Schweigt der Server, hinge sonst alles daran (Wiederholung,
 * "Lade ..." in RequireAuth, 401-Retry). Bewusst großzügig: Der Server rotiert das Cookie schon beim Einlösen, ein zu
 * knapper Abbruch könnte eines verbrauchen, dessen Antwort nie ankommt.
 */
export const REFRESH_TIMEOUT_MS = 20_000;

/** Nur diese Antworten von /auth/refresh bedeuten: Das Refresh-Cookie ist wirklich ungültig. */
const REFRESH_REJECTED_STATUS = new Set([400, 401, 403]);

/**
 * Laufender `refresh()`, den gleichzeitige Aufrufer teilen (unter StrictMode schon beim Start): Der Server rotiert das
 * Cookie bei jeder Einlösung und nimmt das verbrauchte nur noch kurz an. Modulweit statt im Store, weil ein Promise
 * keine Re-Renders auslösen soll, aber alle Aufrufer dieselbe Instanz sehen müssen.
 */
let inFlightRefresh: Promise<boolean> | null = null;

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  user: null,
  status: "unknown",
  mfaToken: null,

  async login(username, password) {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, client_type: "web" }),
    });
    if (res.status === 202) {
      const body = await parseJsonResponse<{ mfa_token: string }>(res);
      set({ mfaToken: body.mfa_token });
      return { ok: false, mfaToken: body.mfa_token };
    }
    if (!res.ok) {
      const error = await authErrorText(res);
      set({ status: "anonymous" });
      return { ok: false, error };
    }
    const body = await parseJsonResponse<{ access_token: string; user: CurrentUser }>(res);
    set({ accessToken: body.access_token, user: body.user, status: "authenticated", mfaToken: null });
    return { ok: true };
  },

  async bootstrap(username, password, setupCode) {
    const res = await fetch("/api/v1/auth/bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, setup_code: setupCode }),
    });
    if (!res.ok) return { ok: false, error: await authErrorText(res) };
    // Bootstrap legt nur den Owner an und liefert kein Token; `login()` danach ist derselbe Weg wie jeder spätere
    // Login, statt die Token-Ausgabe hier zu duplizieren.
    return get().login(username, password) as Promise<{ ok: true } | { ok: false; error: string }>;
  },

  async submitMfa(code) {
    const mfaToken = get().mfaToken;
    if (!mfaToken) return { ok: false, error: "Kein MFA-Vorgang aktiv." };
    const res = await fetch("/api/v1/auth/mfa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mfa_token: mfaToken, code }),
    });
    if (!res.ok) {
      const error = await authErrorText(res);
      // 429: zu viele falsche Codes (das Token ist verbraucht) oder die Anmeldung ist gesperrt: so oder so
      // zurück zum Passwort.
      if (res.status === 429) set({ mfaToken: null });
      return { ok: false, error };
    }
    const body = await parseJsonResponse<{ access_token: string; user: CurrentUser }>(res);
    set({ accessToken: body.access_token, user: body.user, status: "authenticated", mfaToken: null });
    return { ok: true };
  },

  refresh() {
    // Hängt sich an einen laufenden Refresh an. Bewusst kein `async`: Die Zuweisung an `inFlightRefresh` muss vor
    // jedem `await` passieren, sonst sähen zwei im selben Tick gestartete Aufrufe beide `null`.
    if (inFlightRefresh) return inFlightRefresh;

    inFlightRefresh = (async () => {
      // Abbruch nach REFRESH_TIMEOUT_MS, gilt auch fürs Lesen der Antwort (unten).
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), REFRESH_TIMEOUT_MS);
      try {
        let res: Response;
        try {
          res = await fetch("/api/v1/auth/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
            signal: controller.signal,
          });
        } catch {
          // Verbindung abgelehnt/abgebrochen oder keine Antwort in REFRESH_TIMEOUT_MS (Neustart, WLAN weg):
          // keine Aussage über das Cookie, Zustand behalten, der Aufrufer versucht es später.
          throw new ServerUnavailableError();
        }
        if (REFRESH_REJECTED_STATUS.has(res.status)) {
          set({ accessToken: null, user: null, status: "anonymous" });
          return false;
        }
        // Alles andere außer Erfolg (5xx, 429, 404 vom Proxy) sagt nichts über das Cookie: nicht abmelden, aber
        // den Grund des Servers weitergeben (siehe `ServerUnavailableError`).
        if (!res.ok) throw await refreshFailure(res);
        let body: { access_token?: string; user?: CurrentUser };
        try {
          body = await parseJsonResponse<{ access_token?: string; user?: CurrentUser }>(res);
        } catch {
          throw new ServerUnavailableError();
        }
        if (!body.access_token || !body.user) throw new ServerUnavailableError();
        set({ accessToken: body.access_token, user: body.user, status: "authenticated" });
        return true;
      } finally {
        clearTimeout(timeout);
      }
    })().finally(() => {
      inFlightRefresh = null;
    });
    return inFlightRefresh;
  },

  async logout() {
    await fetch("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
    set({ accessToken: null, user: null, status: "anonymous", mfaToken: null });
  },

  hasPermission(permission) {
    const user = get().user;
    if (!user) return false;
    if (user.is_owner) return true;
    return matchesPermission(user.permissions, permission);
  },
}));
