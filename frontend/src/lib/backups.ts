/**
 * Sicherungen (Einstellungen -> System): Typen zu GET /system/backups und kleine Helfer.
 *
 * Passwoerter laufen NIE ueber react-query (kein Query-Key, kein Mutations-Cache): die Karte
 * ruft `api.put/post/delete` direkt und leert die Felder danach.
 */
import { api } from "./api";

export const PASSWORD_MIN_LENGTH = 12;

export interface BackupItem {
  name: string;
  size: number;
  created_at: string | null;
  app_version: string | null;
  key_id: string | null;
  mode: "schluessel" | "passwort" | null;
  status: "ok" | "ungeprueft" | "beschaedigt";
  checked_at?: string | null;
  check_ok?: boolean | null;
  key_current: boolean;
}

export interface BackupConfig {
  enabled: boolean;
  schedule: string;
  keep: number;
  dir: string;
  include_runs: boolean;
}

export interface BackupOverview {
  config: BackupConfig;
  key: { key_id: string; created_at: string | null } | null;
  target: {
    dir: string;
    default_dir: string;
    external_root: string;
    external_available: boolean;
    same_storage_as_data: boolean;
    free_bytes: number | null;
    error: string | null;
  };
  backups: BackupItem[];
  last_run: {
    at: string;
    ok: boolean;
    trigger: string;
    name: string | null;
    size: number | null;
    error: string | null;
    /** Hinweise zu einem Lauf, der trotzdem gelang (z. B. nicht gesicherte Verknüpfungen). Ältere Einträge: fehlt. */
    warnings?: string[];
  } | null;
  running: { kind: string; started_at: string } | null;
  sqlite: boolean;
  limits: { keep_min: number; keep_max: number; password_min_length: number };
}

export interface DownloadTicket {
  ticket: string;
  /** Eigene ID zum Abfragen des Stands (taugt nicht zum Herunterladen, steht in der Adresse). */
  job_id: string;
  status: "building" | "ready" | "failed";
  filename: string;
  size: number | null;
  error: string | null;
  expires_in: number;
  url: string;
}

export interface KeyResult {
  key_id: string;
  recipient: string;
  recovery_key: string;
}

/** Ordner so schreiben, wie das Backend ihn vergleicht: leer = Standardordner, ohne Schrägstrich am Ende. */
function normalizeDir(dir: string, defaultDir: string): string {
  const trimmed = dir.trim().replace(/\/{2,}/g, "/");
  if (!trimmed) return defaultDir;
  return trimmed.length > 1 ? trimmed.replace(/\/+$/, "") : trimmed;
}

/** Änderungen an den Einstellungen, die Sicherungen gefährden und darum das Anmeldepasswort
 *  brauchen (wie `protected_changes` im Backend): weniger behalten, ausschalten, anderer Ordner. */
export function protectedConfigChanges(
  old: Pick<BackupConfig, "enabled" | "keep" | "dir">,
  next: { enabled: boolean; keep: number; dir: string },
  defaultDir: string,
): string[] {
  const reasons: string[] = [];
  if (Number.isFinite(next.keep) && next.keep < old.keep) reasons.push("weniger Sicherungen behalten");
  if (old.enabled && !next.enabled) reasons.push("die automatische Sicherung ausschalten");
  if (normalizeDir(next.dir, defaultDir) !== normalizeDir(old.dir, defaultDir)) reasons.push("den Ordner ändern");
  return reasons;
}

/** Fehlertext fuer ein neues Passwort mit Wiederholung, `null` = in Ordnung. */
export function newPasswordProblem(password: string, repeat: string, min = PASSWORD_MIN_LENGTH): string | null {
  if (password.length < min) return `Mindestens ${min} Zeichen.`;
  if (password !== repeat) return "Die beiden Eingaben stimmen nicht überein.";
  return null;
}

export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return "–";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString("de-DE", { maximumFractionDigits: value < 10 ? 1 : 0 })} ${units[unit]}`;
}

export function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  return date.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}

/** Normaler Browser-Download (die Datei landet nie komplett im Speicher der Seite). Als Objekt,
 *  damit Tests es ersetzen koennen. */
export const browserDownload = {
  start(url: string, filename?: string): void {
    const link = document.createElement("a");
    link.href = url;
    if (filename) link.download = filename;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  },
};

/** Text der Datei "Wiederherstellungsschluessel speichern". */
export function recoveryKeyFile(key: KeyResult, productName = "Nodvard Deck"): string {
  return [
    `${productName} – Wiederherstellungsschlüssel für Sicherungen`,
    `Kennung: ${key.key_id}`,
    "",
    key.recovery_key,
    "",
    "Damit lassen sich alle Sicherungen mit dieser Kennung öffnen, auch ohne das Sicherungspasswort.",
    "Gut aufbewahren (Passwortmanager, ausgedruckt im Schrank) und niemandem geben.",
    "",
    "Notfall ohne Nodvard Deck: die ersten zwei Zeilen der .ndbak-Datei abschneiden und mit age öffnen:",
    "  tail -n +3 sicherung.ndbak > sicherung.age",
    "  age -d -i diese-datei.txt -o sicherung.tar.gz sicherung.age",
    "",
  ].join("\n");
}

const POLL_MS = 1000;

/** Fragt den Stand eines Download-Tickets ab, bis die Datei fertig ist (oder scheitert). */
export async function waitForTicket(ticket: DownloadTicket, { pollMs = POLL_MS, signal }: { pollMs?: number; signal?: AbortSignal } = {}): Promise<DownloadTicket> {
  let current = ticket;
  while (current.status === "building") {
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    if (signal?.aborted) throw new Error("Abgebrochen.");
    current = await api.get<DownloadTicket>(`/system/backups/download-jobs/${ticket.job_id}`);
  }
  if (current.status === "failed") throw new Error(current.error || "Die Sicherung konnte nicht erstellt werden.");
  return current;
}
