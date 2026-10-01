/**
 * Auth-Zustand -- docs/00-DECISIONS.md D-07, docs/04-API.md §1/§2.
 *
 * Der Access-Token lebt NUR im Speicher (D-07: "im Speicher des Clients"), nie in
 * localStorage/sessionStorage -- ein XSS-Angriff, der localStorage lesen kann, koennte
 * sonst einen 30 Tage gueltigen Zugang stehlen statt nur einen 15-Minuten-Token. Der
 * Refresh-Token fuer Web ist ein HttpOnly-Cookie, den JavaScript ohnehin nie sieht.
 *
 * Zustand (nicht Redux, D-03) fuer den globalen Auth-Zustand -- `apiFetch` (lib/api.ts)
 * liest ihn ausserhalb von React-Komponenten ueber `useAuthStore.getState()`.
 */
import { create } from "zustand";

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
 * Fehlertext einer abgelehnten Auth-Antwort. Der Server schickt `detail` als deutschen
 * Satz -- bei 429 z. B. "Zu viele Fehlversuche. Bitte in 4 Minuten erneut versuchen."
 * Nur ein String wird uebernommen: ein 422 liefert `detail` als Liste,
 * und die als React-Kind zu rendern liesse die Anmeldeseite abstuerzen.
 */
export async function authErrorText(res: Response): Promise<string> {
  const body = await parseJsonResponse<{ detail?: unknown }>(res).catch(() => ({}) as { detail?: unknown });
  if (typeof body.detail === "string" && body.detail) return body.detail;
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
 * `refresh()` wirft das, wenn die Anmeldung nicht erneuert werden konnte, das Cookie aber
 * NICHT als ungueltig abgelehnt wurde: Netzwerkfehler, Zeitlimit, 5xx (z. B. 502/503/504
 * vom Proxy, waehrend der Container nach einem Deploy neu startet) oder eine unlesbare
 * Antwort. Die Anmeldung ist dann NICHT ungueltig -- der Zustand im Store bleibt, wie er
 * war, und der Aufrufer versucht es spaeter noch einmal. Nur `false` (Server lehnt das
 * Refresh-Cookie ab) bedeutet "abgemeldet".
 *
 * Ohne `status` ist der Server wirklich "nicht erreichbar" (Netzwerkfehler, Zeitlimit,
 * 502/503/504 ohne lesbaren Grund). Mit `status` hat er geantwortet und nennt einen Grund
 * (z. B. 507 "Speicherplatz voll" oder ein 500): `message` ist dann sein Text (oder
 * "HTTP <status>"), `detail` der geparste Body -- der Aufrufer zeigt das statt des festen
 * "nicht erreichbar"-Texts an, meldet aber ebenfalls nicht ab.
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

/** Antworten, die ein Proxy schickt, solange der Server nicht (fertig) laeuft. */
const SERVER_DOWN_STATUS = new Set([502, 503, 504]);

/**
 * Antwort von /auth/refresh, die weder Erfolg noch Ablehnung des Cookies ist: Text und
 * Body lesen. 502/503/504 ohne lesbaren Grund (HTML vom Proxy) heissen weiter "nicht
 * erreichbar"; alles andere traegt Status und Grund des Servers.
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
 * So lange wartet `refresh()` hoechstens auf die Antwort. Steht die Verbindung, der Server
 * antwortet aber nicht (Container wird gerade ersetzt, schlechtes Netz am Handy), wuerde
 * das Promise sonst nie enden -- und mit ihm alles, was sich daran haengt (Wiederholung
 * in lib/tokenRefresh.ts, "Lade ..." in RequireAuth, jeder 401-Retry in lib/api.ts).
 * Bewusst grosszuegig: der Server rotiert das Cookie schon beim Einloesen, eine zu
 * knapp abgebrochene Anfrage koennte ein Cookie verbrauchen, dessen Antwort wir nie sehen.
 */
export const REFRESH_TIMEOUT_MS = 20_000;

/** Nur diese Antworten von /auth/refresh heissen: das Refresh-Cookie ist wirklich ungueltig. */
const REFRESH_REJECTED_STATUS = new Set([400, 401, 403]);

/**
 * Geteilter In-Flight-Zustand fuer `refresh()` -- das Race schlaegt unter React 18
 * StrictMode sogar bei JEDER Erstinstallation deterministisch zu: `services/auth.py::
 * refresh_access_token()` rotiert das Refresh-Token bei jeder Einloesung (sofortiger
 * Widerruf + Neuausgabe, kein Gnadenfenster) -- ein ZWEITER gleichzeitiger Aufruf mit
 * demselben (durch den ersten bereits verbrauchten) Cookie bekam bisher sofort 401 und
 * warf den Nutzer auf den Login-Bildschirm zurueck, obwohl der erste Aufruf erfolgreich
 * war. Modulweite Variable statt Store-State: ein Promise gehoert nicht in reaktiven
 * Zustand (loest sonst unnoetige Re-Renders aus, sobald es sich aendert), muss aber
 * ausserhalb von `create()` leben, damit ALLE gleichzeitigen `refresh()`-Aufrufer
 * (RequireAuth, jeder von `apiFetch` ausgeloeste 401-Retry) dieselbe Instanz sehen.
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
    // Erstellt nur den Owner (api/v1/auth.py::bootstrap) -- kein Token in der Antwort.
    // `login()` danach ist derselbe Weg, den jeder spaetere Login auch nimmt, statt
    // die Token-Ausgabe hier zu duplizieren.
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
      // 429: zu viele falsche Codes (das Token ist verbraucht) oder die Anmeldung ist
      // gesperrt -- so oder so muss man sich neu anmelden, also zurueck zum Passwort.
      if (res.status === 429) set({ mfaToken: null });
      return { ok: false, error };
    }
    const body = await parseJsonResponse<{ access_token: string; user: CurrentUser }>(res);
    set({ accessToken: body.access_token, user: body.user, status: "authenticated", mfaToken: null });
    return { ok: true };
  },

  refresh() {
    // Haengt sich an einen bereits laufenden Refresh an, statt einen zweiten mit dem
    // (nach dem ersten erfolgreichen Aufruf bereits verbrauchten) Cookie auszuloesen --
    // siehe `inFlightRefresh`-Kommentar oben. Bewusst KEIN `async`/`await` hier: die
    // Zuweisung an `inFlightRefresh` muss SYNCHRON vor jedem `await`-Punkt passieren,
    // sonst koennten zwei im selben Tick gestartete Aufrufe die Pruefung `if
    // (inFlightRefresh)` beide noch als `null` sehen.
    if (inFlightRefresh) return inFlightRefresh;

    inFlightRefresh = (async () => {
      // Abbruch nach REFRESH_TIMEOUT_MS -- gilt auch fuers Lesen der Antwort (unten).
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
          // Verbindung abgelehnt/abgebrochen oder keine Antwort in REFRESH_TIMEOUT_MS
          // (Container startet neu, WLAN weg): keine Aussage ueber das Cookie -- Zustand
          // behalten, Aufrufer versucht es spaeter.
          throw new ServerUnavailableError();
        }
        if (REFRESH_REJECTED_STATUS.has(res.status)) {
          set({ accessToken: null, user: null, status: "anonymous" });
          return false;
        }
        // Alles andere ausser Erfolg (5xx, aber auch 429 oder ein 404 vom Proxy) sagt
        // nichts darueber, ob das Cookie noch gilt -- nicht abmelden, aber den Grund des
        // Servers weitergeben (siehe `ServerUnavailableError`).
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
