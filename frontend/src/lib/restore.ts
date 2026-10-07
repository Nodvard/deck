/**
 * Wiederherstellen einer Sicherung (Einstellungen -> System und Einrichtungs-Assistent):
 * Typen, die Aufrufe der beiden Wege und das Warten auf den Neustart.
 *
 * Zwei Wege, derselbe Ablauf:
 *  - `owner`: `/system/restore/*`, der Owner bestaetigt mit seinem Anmeldepasswort,
 *  - `setup`: `/auth/bootstrap/restore/*`, nur ohne Konto, mit dem Einrichtungscode im Kopf
 *    `X-Setup-Code` bei JEDEM Aufruf.
 *
 * Passwoerter, Schluessel und der Code laufen NIE ueber react-query (kein Query-Key, kein
 * Mutations-Cache): die Aufrufer reichen sie nur als Argument durch und leeren ihre Felder danach.
 *
 * Hochladen ist ein ROHER Strom (`application/octet-stream`, kein Formular), damit der Server die Datei
 * nie ganz im Speicher haelt. `fetch` kennt keinen Upload-Fortschritt, deshalb `XMLHttpRequest`.
 */
import { SERVER_UNAVAILABLE_TEXT, useAuthStore } from "../state/auth";
import { ApiError, apiFetch, refreshAfterUnauthorized } from "./api";

export type RestoreMode = "owner" | "setup";

export interface RestoreSummary {
  created_at: string | null;
  app_version: string | null;
  instance_id: string | null;
  mode: "passwort" | "schluessel" | null;
  owner_name: string | null;
  users: number | null;
  hosts: number | null;
  extensions: { id: string; version: string }[];
  includes: { runs: boolean; branding: boolean; jwt_secret: boolean };
  warnings: string[];
}

export interface StagedRestore {
  id: string;
  state: "uploading" | "uploaded" | "ready" | "scheduled";
  size: number | null;
  header: { mode: "passwort" | "schluessel"; created_at: string; app_version: string } | null;
  summary: RestoreSummary | null;
  /** Sekunden, nach denen der Zwischenstand verfaellt. */
  expires_in: number;
}

export interface PendingRestore {
  id: string;
  source: "owner" | "bootstrap" | "cli";
  scheduled_at: string;
  expires_in: number;
  sign_out_all: boolean;
  backup: { created_at?: string | null; app_version?: string | null; instance_id?: string | null; owner_name?: string | null };
}

export interface RestoreResult {
  ok: boolean;
  at: string | null;
  message: string | null;
  source: string | null;
  actor: string | null;
  replaced: string | null;
  rolled_back: boolean | null;
}

export interface RestoreStatus {
  pending: PendingRestore | null;
  staged: StagedRestore | null;
  result: RestoreResult | null;
  /** `expires_at`: wann der alte Stand von selbst gelöscht wird (30 Tage nach dem Einspielen). */
  replaced: { name: string; size: number; at: string | null; expires_at?: string | null } | null;
  limits: { max_upload_bytes: number; max_unpacked_bytes: number; expires_in: number };
  busy: boolean;
}

export type RestoreSecret = { password: string } | { recovery_key: string };

/**
 * Anmeldedaten eines Aufrufs: Owner-Passwort bzw. Einrichtungscode. Werden nie gespeichert. `totpCode` nur beim
 * Vormerken, wenn Zwei-Faktor an ist (der Server fragt mit `totp_missing` danach).
 */
export type RestoreAuth = { mode: "owner"; accountPassword: string; totpCode?: string } | { mode: "setup"; setupCode: string };

const OCTET = "application/octet-stream";

/** Kopfzeilen koennen kein Unicode: das Passwort geht prozentkodiert (UTF-8) in `X-Confirm-Password`. */
export function confirmHeader(password: string): string {
  return encodeURIComponent(password);
}

function prefix(auth: RestoreAuth): string {
  return auth.mode === "owner" ? "/system/restore" : "/auth/bootstrap/restore";
}

function guardHeaders(auth: RestoreAuth): Record<string, string> {
  return auth.mode === "setup" ? { "X-Setup-Code": auth.setupCode.trim() } : {};
}

/** Text einer Fehlerantwort (`detail` als Satz oder Liste von Validierungsfehlern). */
export function errorMessage(status: number, text: string): string {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      return body.detail
        .map((item) => (typeof item === "object" && item !== null && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
        .join("; ");
    }
  } catch {
    /* kein JSON */
  }
  if (status === 502 || status === 503 || status === 504) return SERVER_UNAVAILABLE_TEXT;
  return `HTTP ${status}`;
}

export interface UploadRequest {
  url: string;
  file: File;
  headers: Record<string, string>;
  onProgress: (loaded: number, total: number) => void;
  signal?: AbortSignal;
}

export interface UploadResponse {
  status: number;
  text: string;
}

/**
 * Schickt die Datei als rohen Strom mit Fortschritt. Eigenes Objekt, damit Tests es ersetzen koennen
 * (jsdom kennt keinen echten Upload).
 */
export const uploadTransport = {
  send({ url, file, headers, onProgress, signal }: UploadRequest): Promise<UploadResponse> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.setRequestHeader("Content-Type", OCTET);
      for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded, event.total);
      };
      xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText });
      xhr.onerror = () => reject(new ApiError(503, SERVER_UNAVAILABLE_TEXT));
      xhr.onabort = () => reject(new DOMException("Abgebrochen", "AbortError"));
      xhr.ontimeout = () => reject(new ApiError(503, SERVER_UNAVAILABLE_TEXT));
      if (signal) {
        if (signal.aborted) {
          xhr.abort();
          return;
        }
        signal.addEventListener("abort", () => xhr.abort(), { once: true });
      }
      xhr.send(file);
    });
  },
};

export async function uploadBackup(
  file: File,
  auth: RestoreAuth,
  { onProgress, signal }: { onProgress: (loaded: number, total: number) => void; signal?: AbortSignal },
): Promise<StagedRestore> {
  const send = () =>
    uploadTransport.send({
      url: `/api/v1${prefix(auth)}/upload`,
      file,
      headers: {
        ...guardHeaders(auth),
        ...(auth.mode === "owner" ? { "X-Confirm-Password": confirmHeader(auth.accountPassword) } : {}),
        ...(useAuthStore.getState().accessToken ? { Authorization: `Bearer ${useAuthStore.getState().accessToken}` } : {}),
      },
      onProgress,
      signal,
    });
  let res = await send();
  if (res.status === 401 && auth.mode === "owner") {
    // Der Zugang ist waehrend des Hochladens abgelaufen: einmal still erneuern, dann noch einmal
    // (dieselbe Erneuerung wie `apiFetch`; antwortet der Server nicht, kommt ein ApiError mit Klartext).
    if (await refreshAfterUnauthorized()) res = await send();
  }
  if (res.status < 200 || res.status >= 300) throw new ApiError(res.status, errorMessage(res.status, res.text));
  return JSON.parse(res.text) as StagedRestore;
}

export function inspectBackup(id: string, secret: RestoreSecret, auth: RestoreAuth): Promise<StagedRestore> {
  return apiFetch<StagedRestore>(`${prefix(auth)}/${id}/inspect`, {
    method: "POST",
    headers: guardHeaders(auth),
    body: JSON.stringify(secret),
  });
}

export function scheduleRestore(id: string, auth: RestoreAuth): Promise<PendingRestore> {
  return apiFetch<PendingRestore>(`${prefix(auth)}/${id}/schedule`, {
    method: "POST",
    headers: guardHeaders(auth),
    body: JSON.stringify(
      auth.mode === "owner"
        ? { current_password: auth.accountPassword, sign_out_all: true, ...(auth.totpCode ? { totp_code: auth.totpCode } : {}) }
        : { sign_out_all: true },
    ),
  });
}

export function cancelRestore(auth: RestoreAuth): Promise<void> {
  return apiFetch<void>(`${prefix(auth)}/pending`, { method: "DELETE", headers: guardHeaders(auth) });
}

export function restartServer(auth: RestoreAuth): Promise<{ restarting: boolean; exit_code: number }> {
  return apiFetch(auth.mode === "owner" ? "/system/restart" : "/auth/bootstrap/restart", {
    method: "POST",
    headers: guardHeaders(auth),
    body: auth.mode === "owner" ? JSON.stringify({ current_password: auth.accountPassword }) : undefined,
  });
}

export function deleteReplaced(accountPassword: string): Promise<void> {
  return apiFetch<void>("/system/restore/replaced", { method: "DELETE", body: JSON.stringify({ current_password: accountPassword }) });
}

// ---------------------------------------------------------------------------
// Warten auf den Neustart
// ---------------------------------------------------------------------------

export interface Health {
  status?: string;
  uptime_s?: number;
  /** Laufende Version (`GET /api/v1/health`); daran sieht der Update-Ablauf, welche Version nach dem Neustart läuft. */
  version?: string;
}

/** Eigenes Objekt, damit Tests Gesundheitsabfrage und Seitenwechsel ersetzen koennen. */
export const restartProbe = {
  async health(): Promise<Health | null> {
    try {
      const res = await fetch("/api/v1/health", { cache: "no-store" });
      if (!res.ok) return null;
      return (await res.json()) as Health;
    } catch {
      return null;
    }
  },
};

export const browserNavigation = {
  /** Harter Seitenwechsel: danach ist alles neu geladen (Anmeldung, Aussehen aus der eingespielten Sicherung). */
  assign(url: string): void {
    window.location.assign(url);
  },
};

/** Abfragetakt und Zeitlimit; als Objekt, damit Tests sie verkuerzen koennen. */
export const restartTiming = { pollMs: 2000, timeoutMs: 10 * 60 * 1000 };

/**
 * Wartet, bis Nodvard Deck nach dem Neustart wieder antwortet. Erkannt wird der Neustart daran, dass die
 * Abfrage einmal scheitert ODER die Laufzeit (`uptime_s`) kleiner geworden ist als vorher (ein sehr
 * schneller Neustart faellt zwischen zwei Abfragen). Eine Antwort VOR dem Neustart zaehlt nicht.
 * Gibt `true` zurueck, wenn der Dienst wieder da ist, `false` nach dem Zeitlimit.
 *
 * `timeoutMs`: eigenes Zeitlimit (sonst `restartTiming.timeoutMs`). `ready`: eigene Erkennung, fuer Aufrufer, die
 * den Ausfall selbst schon gesehen haben (der Update-Helfer schaltet den Container um): dann zaehlt jede gesunde
 * Antwort, fuer die `ready` zutrifft, auch ohne einen weiteren Ausfall.
 */
export async function waitForRestart(
  baselineUptime: number | null,
  {
    signal, onTick, timeoutMs = restartTiming.timeoutMs, ready,
  }: { signal?: AbortSignal; onTick?: (seconds: number) => void; timeoutMs?: number; ready?: (health: Health) => boolean } = {},
): Promise<boolean> {
  const { pollMs } = restartTiming;
  const started = Date.now();
  let sawDown = false;
  while (Date.now() - started < timeoutMs) {
    if (signal?.aborted) return false;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    const health = await restartProbe.health();
    onTick?.(Math.round((Date.now() - started) / 1000));
    if (health === null) {
      sawDown = true;
      continue;
    }
    if (ready) {
      if (health.status === "ok" && ready(health)) return true;
      continue;
    }
    const restarted = baselineUptime !== null && typeof health.uptime_s === "number" && health.uptime_s < baselineUptime;
    if (health.status === "ok" && (sawDown || restarted)) return true;
  }
  return false;
}

/** Laufzeit vor dem Neustart (fuer `waitForRestart`); `null`, wenn die Abfrage nicht klappt. */
export async function readUptime(): Promise<number | null> {
  const health = await restartProbe.health();
  return typeof health?.uptime_s === "number" ? health.uptime_s : null;
}

/** Nach dem Neustart im Assistenten: gibt es jetzt ein Konto, oder hat die Wiederherstellung nicht geklappt? */
export async function bootstrapAfterRestart(): Promise<{ needed: boolean; restore?: { ok: false; message: string | null; at: string | null } } | null> {
  try {
    const res = await fetch("/api/v1/auth/bootstrap", { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as { needed: boolean; restore?: { ok: false; message: string | null; at: string | null } };
  } catch {
    return null;
  }
}

/** Sekunden als "12 Min." bzw. "45 Sek." fuer die Anzeige. */
export function formatDuration(seconds: number): string {
  if (seconds >= 120) return `${Math.round(seconds / 60)} Min.`;
  if (seconds >= 60) return "1 Min.";
  return `${Math.max(0, Math.round(seconds))} Sek.`;
}
