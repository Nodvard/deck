/**
 * Server & Zugaenge (Einstellungen -> Server & Zugaenge): Typen der Antworten aus
 * `api/v1/hosts.py`, `host_access.py` und `services/host_check.py`, dazu die Abfragen
 * (react-query) und ein paar kleine Helfer fuer die Anzeige.
 *
 * WICHTIG: Schluessel und Passwoerter, die jemand eintippt oder einfuegt, gehen NIE ueber
 * `useMutation` (dessen Eingabe bliebe im Mutations-Cache liegen) und nie in einen
 * Query-Key -- die Formulare rufen `api.post` direkt auf und leeren das Feld vorher.
 * Die einzige Antwort mit Schluesselbezug ist der OEFFENTLICHE Teil (`public_key`).
 */
import { useQueries, useQuery, type QueryClient } from "@tanstack/react-query";

import { api, ApiError } from "./api";
import { hostHealth, type HostCredentialSummary, type HostOut } from "./overview";

export type { HostCredentialSummary, HostOut };

export type CredentialKind = "ssh_key" | "ssh_password" | "api_token";

export interface CredentialOut {
  id: string;
  host_id: string;
  kind: CredentialKind;
  username: string;
  port: number;
  is_default: boolean;
  created_at: string;
}

export interface MakeDefaultOut extends CredentialOut {
  /** Gesetzt, wenn der alte Zugang geloescht wurde: sein Schluessel steht weiter auf dem Server. */
  notice: string | null;
}

export interface GeneratedKeyOut {
  credential: CredentialOut;
  public_key: string;
  fingerprint: string;
}

export interface SetupOut {
  username: string;
  public_key: string;
  fingerprint: string;
  one_liner: string;
  script: string;
  notes: string[];
  groups: string[];
  sudo: boolean;
}

export interface RequirementOut {
  ext_id: string;
  id: string;
  label: string;
  check_command: string | null;
  ok_text: string;
  fail_hint: string;
  unix_group: string | null;
  needs_root: boolean;
  root_reason: string | null;
  order: number;
}

export type CheckStatus = "ok" | "warn" | "fail" | "skipped" | "confirm";

export interface CheckItem {
  id: string;
  label: string;
  status: CheckStatus;
  detail: string;
  hint: string;
}

export interface HostKeyInfo {
  status: "known" | "new" | "changed";
  key_type: string;
  fingerprint: string;
  expected: string | null;
}

export interface ConnectionCheck {
  ok: boolean;
  checked_at: string;
  items: CheckItem[];
  host_key: HostKeyInfo | null;
  os: { pretty_name: string | null; arch: string | null; model: string | null } | null;
  credential_id: string | null;
}

export interface KnownKeyOut {
  key_type: string;
  fingerprint: string;
  first_seen_at: string;
  accepted_by_user_id: string | null;
  accepted_by_label: string | null;
}

export interface GroupOut {
  id: string;
  name: string;
  description: string;
}

export const CREDENTIAL_KIND_LABEL: Record<string, string> = {
  ssh_key: "Schlüssel",
  ssh_password: "Passwort",
  api_token: "Zugriffstoken",
};

/** Nur SSH-Zugaenge gehoeren auf diese Seite (Zugriffstoken kennt keine Oberflaeche). */
export function isSshCredential(credential: { kind: string }): boolean {
  return credential.kind === "ssh_key" || credential.kind === "ssh_password";
}

/**
 * Wie weit der Zugang eines Servers belegt ist -- aus dem Zustand des Servers und, falls die Person
 * gerade „Verbindung pruefen“ gedrueckt hat, aus dessen Ergebnis (`login`). Ein gespeicherter Zugang
 * allein beweist nichts: Bei einem Schluessel kann der Befehl auf dem Server noch fehlen, ein
 * Passwort kann falsch sein. „Erreichbar“ heisst im Kern nur: der SSH-Port nimmt Verbindungen an --
 * das sieht der Erreichbarkeits-Job auch ohne Anmeldung. Gruen wird ein SSH-Zugang deshalb erst mit
 * einer belegten Anmeldung (`login_ok_at`); antwortet der Server, ohne dass sie je geklappt hat,
 * heisst der Zustand `unconfirmed`.
 */
export type AccessState = "ok" | "login-failed" | "never-answered" | "no-answer" | "unchecked" | "unconfirmed";

type AccessHost = Pick<HostOut, "status" | "last_seen_at"> & Partial<Pick<HostOut, "login_ok_at" | "credential">>;

/** Hat der Server einen SSH-Zugang, dessen Anmeldung noch nie belegt wurde? */
export function loginUnproven(host: Partial<Pick<HostOut, "credential" | "login_ok_at">>): boolean {
  return !!host.credential && isSshCredential(host.credential) && !host.login_ok_at;
}

export function accessState(host: AccessHost, login: "ok" | "fail" | null = null): AccessState {
  if (login === "ok") return "ok";
  if (login === "fail") return "login-failed";
  const health = hostHealth(host.status);
  if (health === "online") return loginUnproven(host) ? "unconfirmed" : "ok";
  if (neverAnswered(host)) return health === "unknown" ? "unchecked" : "never-answered";
  return health === "unknown" ? "unchecked" : "no-answer";
}

/** Ein Server, der noch nie geantwortet hat: es gibt keinen Beleg, dass er je erreichbar war. */
export function neverAnswered(host: Pick<HostOut, "status" | "last_seen_at">): boolean {
  return hostHealth(host.status) !== "online" && !host.last_seen_at;
}

/** Ergebnis der Anmeldung in einer Verbindungspruefung: `null`, wenn die Pruefung sie gar nicht erreicht hat. */
export function loginOutcome(result: ConnectionCheck): "ok" | "fail" | null {
  const item = result.items.find((i) => i.id === "login");
  if (!item || item.status === "skipped") return null;
  return item.status === "ok" ? "ok" : "fail";
}

/** „Schluessel · lattice“ -- die kurze Beschriftung eines Zugangs. */
export function accessLabel(credential: Pick<HostCredentialSummary, "kind" | "username">): string {
  return `${CREDENTIAL_KIND_LABEL[credential.kind] ?? credential.kind} · ${credential.username}`;
}

// ---------------------------------------------------------------------------
// Abfragen
// ---------------------------------------------------------------------------

export function useHost(hostId: string) {
  return useQuery({ queryKey: ["hosts", hostId], queryFn: () => api.get<HostOut>(`/hosts/${hostId}`) });
}

export function useCredentials(hostId: string) {
  return useQuery({
    queryKey: ["hosts", hostId, "credentials"],
    queryFn: () => api.get<CredentialOut[]>(`/hosts/${hostId}/credentials`),
  });
}

export function useKnownKeys(hostId: string) {
  return useQuery({
    queryKey: ["hosts", hostId, "known-hosts"],
    queryFn: () => api.get<KnownKeyOut[]>(`/hosts/${hostId}/known-hosts`),
  });
}

export function useRequirements(hostId: string) {
  return useQuery({
    queryKey: ["hosts", hostId, "requirements"],
    queryFn: () => api.get<RequirementOut[]>(`/hosts/${hostId}/requirements`),
  });
}

export function useGroups() {
  return useQuery({ queryKey: ["host-groups"], queryFn: () => api.get<GroupOut[]>("/host-groups") });
}

/** Wer in welcher Gruppe ist: je Gruppe eine Abfrage `GET /hosts?group=<id>` (die Gruppen-API
 * selbst nennt keine Mitglieder). Ergebnis: Gruppen-ID -> IDs der Server. */
export function useGroupMembers(groupIds: string[]): { members: Record<string, string[]>; isLoading: boolean } {
  const results = useQueries({
    queries: groupIds.map((id) => ({
      queryKey: ["hosts", "group", id],
      queryFn: () => api.get<HostOut[]>(`/hosts?group=${encodeURIComponent(id)}`),
    })),
  });
  const members: Record<string, string[]> = {};
  groupIds.forEach((id, i) => {
    members[id] = (results[i]?.data ?? []).map((h) => h.id);
  });
  return { members, isLoading: results.some((r) => r.isLoading) };
}

/** Der Einrichtungsbefehl. Aendert nichts auf dem Server (GET); die Wahl von sudo und Gruppen steht im Key. */
export function useSetup(hostId: string, credentialId: string, options: { sudo: boolean; groups: string[]; enabled: boolean }) {
  const groups = [...options.groups].sort();
  const query = new URLSearchParams();
  query.set("sudo", String(options.sudo));
  for (const g of groups) query.append("groups", g);
  return useQuery({
    queryKey: ["hosts", hostId, "credentials", credentialId, "setup", options.sudo, groups],
    queryFn: () => api.get<SetupOut>(`/hosts/${hostId}/credentials/${credentialId}/setup?${query.toString()}`),
    enabled: options.enabled,
    retry: false,
  });
}

/** Nach jeder Aenderung an einem Server: Liste, Einzelansicht, Zugaenge und Schluessel neu laden. */
export function refreshHosts(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: ["hosts"] });
}

// ---------------------------------------------------------------------------
// Helfer fuer die Anzeige
// ---------------------------------------------------------------------------

/**
 * Die Meldungen des Backends (422) je Feld: `{name: "Kurzname: ...", address: "..."}`. Die deutschen
 * Texte kommen vom Backend (`api/errors.py` schneidet das englische Praefix ab) -- hier wird nur
 * zugeordnet. Bei Listenfeldern (`tags.0`) zaehlt das Feld davor. Alles andere (409, Text): leer.
 */
export function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError) || err.status !== 422) return {};
  const body = err.detail;
  const items = typeof body === "object" && body !== null ? (body as { detail?: unknown }).detail : undefined;
  if (!Array.isArray(items)) return {};
  const out: Record<string, string> = {};
  for (const item of items) {
    if (typeof item !== "object" || item === null) continue;
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const field = loc.find((part) => typeof part === "string" && !["body", "query", "path"].includes(part));
    if (typeof field === "string" && !(field in out)) out[field] = msg;
  }
  return out;
}

/** Kurzfassung eines Pruefergebnisses fuer die Liste: „4 von 5 in Ordnung“. */
export function checkSummary(result: ConnectionCheck): string {
  if (result.items.some((i) => i.status === "confirm")) return "Server-Schlüssel noch nicht bestätigt";
  const counted = result.items.filter((i) => i.status !== "skipped");
  const ok = counted.filter((i) => i.status === "ok").length;
  if (ok === counted.length) return "Alles in Ordnung";
  return `${ok} von ${counted.length} in Ordnung`;
}

export interface TextPart {
  text: string;
  code: boolean;
}

// Befehle, die in Hinweisen vorkommen: in „…“ (sudo apt install …) oder der Vergleich des Fingerabdrucks.
const COMMAND_RE = /„((?:sudo|apt|systemctl|ssh-keygen|usermod|raspi-config)\b[^“]*)“|(ssh-keygen -lf \S+)/g;

/** Zerlegt einen Hinweistext, damit Befehle als `<code>` (mit Kopierknopf) erscheinen koennen. */
export function splitCommands(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const match of text.matchAll(COMMAND_RE)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: text.slice(last, index), code: false });
    parts.push({ text: match[1] ?? match[2] ?? "", code: true });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), code: false });
  return parts;
}

/** `30.09.2026` -- leer bei einem ungueltigen Wert. */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}
