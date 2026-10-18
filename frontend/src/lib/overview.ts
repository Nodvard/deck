/**
 * Daten für die Startseite: Hosts und Live-Metriken aus dem Kern, Dienste/Backups/Freigaben/Warnungen aus GET /overview
 * (herstellerneutral, backend api/v1/overview.py).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";

/** Der Standard-Zugang eines Servers (nie ein Geheimnis, nur Art, Benutzer und Port). */
export interface HostCredentialSummary {
  id: string;
  kind: "ssh_key" | "ssh_password" | "api_token";
  username: string;
  port: number;
}

export interface HostOut {
  id: string;
  name: string;
  display_name: string;
  address: string;
  /** `linux` oder `windows`. */
  os_family: string;
  kind: string | null;
  tags: string[];
  /** Markierungen, die eine Erweiterung verwaltet (nicht von Hand änderbar); Teilmenge von `tags`. */
  managed_tags: string[];
  /** Standard-Zugang, `null` ohne Zugang. */
  credential: HostCredentialSummary | null;
  /** Für Skripte und Nodvard Shield verwenden. */
  is_managed: boolean;
  /** Messwerte sammeln. */
  enabled: boolean;
  status: string;
  /** Wann der Server zuletzt geantwortet hat: der SSH-Port nimmt Verbindungen an, mehr nicht. */
  last_seen_at: string | null;
  /** Wann sich Nodvard Deck zuletzt wirklich mit dem Standard-Zugang angemeldet hat; `null`: noch nie belegt. */
  login_ok_at?: string | null;
  provider_ext_id: string | null;
  /** `server`, `vm` oder `lxc` (Gruppen und Plaketten, `hostKind.tsx`); fehlt bei älteren Servern. */
  category?: string;
  /** Bei einem Gast: auf welchem Host er läuft (Name beim Anbieter). */
  parent_label?: string | null;
  /** Vom Anbieter: `running`, `stopped`, `paused`; `null`, wenn unbekannt. */
  power_state?: string | null;
  /** Seit wann der Anbieter den Server nicht mehr kennt (etwa in Proxmox gelöscht); `null`: er ist da. */
  gone_since?: string | null;
}

export interface MetricsOut {
  values: { cpu_percent?: number; mem_used_bytes?: number; mem_total_bytes?: number; uptime_s?: number };
  sampled_at: string;
}

/** Letzter Messwert eines Servers aus dem Verlauf (`GET /hosts/metrics/latest`); `null` = nicht gemessen. */
export interface HostLatestMetrics {
  cpu: number | null;
  mem: number | null;
  disk: number | null;
  mem_used_bytes: number | null;
  mem_total_bytes: number | null;
  disk_used_bytes: number | null;
  disk_total_bytes: number | null;
  uptime_s: number | null;
  at: string;
  /** Alter beim Abruf, in Sekunden. */
  age_s: number;
  /** Älter als `stale_after_s`: zeigt das Cockpit als „veraltet“. */
  stale: boolean;
}

export interface LatestMetricsOut {
  /** Nur Server mit einem Messwert der letzten Minuten; Hypervisor-Knoten fehlen (sie werden live gefragt). */
  hosts: Record<string, HostLatestMetrics>;
  stale_after_s: number;
}

export interface ServiceOut {
  id: string;
  name: string;
  host: string | null;
  host_id: string | null;
  state: string | null;
  tone: string | null;
  url: string | null;
  image: string | null;
  /** Platzhalter für einen Server, dessen Container nicht lesbar waren (kein Dienst). Fehlt bei älteren Servern. */
  unreachable?: boolean;
}

/** Eine Kachel im Bereich „Apps“ (`GET /overview` -> `apps`): ein erkannter Dienst oder eine von Hand angelegte App.
 * Felder, die es für die andere Art nicht gibt, sind `null`. */
export interface AppTileOut {
  /** Erkannt: wie `ServiceOut.id`. Eigen: die ID aus `/apps` (zum Ändern und Löschen). */
  id: string;
  source: "detected" | "custom";
  name: string;
  url: string | null;
  host: string | null;
  host_id: string | null;
  state: string | null;
  tone: string | null;
  image: string | null;
  /** Nur eigene Apps: Name aus der festen Auswahl (lib/appIcons.ts) oder ein Emoji. */
  icon: string | null;
  /** Nur eigene Apps: `#rrggbb`. */
  color: string | null;
  group: string | null;
  open_in_new_tab: boolean;
  sort_order: number | null;
}

export interface OverviewOut {
  services: ServiceOut[];
  services_running: number;
  /** Namen der Server, deren Container nicht gelesen werden konnten. Fehlt bei älteren Servern. */
  services_unreachable_hosts?: string[];
  /** Eigene Apps zuerst (nach Position), danach die erkannten Dienste. `services` bleibt davon unberührt. */
  apps: AppTileOut[];
  backups: {
    total: number;
    ok: number;
    failed: number;
    running: number;
    unknown: number;
    failed_names: string[];
    /** Verbindungen, die nicht antworten: zählen nicht in `total`. */
    unreachable_names: string[];
  } | null;
  pending_actions: number;
  unread_notifications: number;
  attention: { id: string; ts: string; severity: string; title: string; source_ext_id: string | null }[];
  /** Server, die neu eingerichtet werden sollten: volle sudo-Regel, aber Hilfsskripte fehlen oder sind veraltet. */
  helper_hints?: HelperHint[];
  errors: string[];
  generated_at: number;
}

export interface HelperHint {
  host_id: string;
  host_name: string;
  /** Anzeigenamen der Module, die dort noch über die volle sudo-Regel laufen. */
  modules: string[];
  checked_at: string;
}

/** Blendet den Hinweis für einen Server aus, bis sich die fehlenden Skripte ändern. */
export function useDismissHelperHint() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (hostId: string) => api.post<void>(`/hosts/${encodeURIComponent(hostId)}/helper-hint/dismiss`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["overview"] }),
  });
}

/** „Nodvard Shield und Server-Zustand mit engen Rechten laufen“ (Einzahl: „läuft“). */
export function helperHintText(hint: HelperHint): string {
  const names = hint.modules.length > 1
    ? `${hint.modules.slice(0, -1).join(", ")} und ${hint.modules[hint.modules.length - 1]}`
    : hint.modules[0] ?? "die Module";
  const verb = hint.modules.length === 1 ? "läuft" : "laufen";
  return `${hint.host_name}: Server neu einrichten, damit ${names} mit engen Rechten ${verb}`;
}

export function useOverview(enabled = true) {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => api.get<OverviewOut>("/overview"),
    refetchInterval: 30_000,
    enabled,
  });
}

export function useHosts(enabled = true) {
  return useQuery({
    queryKey: ["hosts", "all"],
    queryFn: () => api.get<HostOut[]>("/hosts"),
    refetchInterval: 30_000,
    enabled,
  });
}

export function useHostMetrics(hostId: string) {
  return useQuery({
    queryKey: ["hosts", hostId, "metrics"],
    queryFn: () => api.get<MetricsOut>(`/hosts/${hostId}/metrics`),
    refetchInterval: 15_000,
    retry: false,
  });
}

/** Letzte Werte aller Server in einer Abfrage, aus dem Verlauf des Kerns: kein SSH beim Laden des Cockpits. */
export function useLatestMetrics(enabled = true) {
  return useQuery({
    queryKey: ["hosts", "metrics", "latest"],
    queryFn: () => api.get<LatestMetricsOut>("/hosts/metrics/latest"),
    refetchInterval: 15_000,
    retry: false,
    enabled,
  });
}

/** IDs der Hosts, für die es eine Terminal-Sitzung gibt (hinterlegte Zugangsdaten);
 * das Backend liefert nur die IDs, keine Objekte. */
export function useTerminalHosts(enabled: boolean) {
  return useQuery({
    queryKey: ["terminal", "hosts"],
    queryFn: () => api.get<string[]>("/terminal/hosts"),
    enabled,
    staleTime: 60_000,
  });
}

const ONLINE = new Set(["up", "running", "online", "ok"]);
const PROBLEM = new Set(["down", "error", "unreachable", "offline", "failed"]);

export type HostHealth = "online" | "stopped" | "problem" | "unknown";

/** Zustand aus `status`; ein laut Anbieter ausgeschalteter Gast (`powerState` `stopped`/`paused`) ist „gestoppt“
 * (grau, „aus“), keine Störung, auch wenn `status` dann `down` ist. */
export function hostHealth(status: string, powerState?: string | null): HostHealth {
  if (powerState === "stopped" || powerState === "paused") return "stopped";
  const s = status.toLowerCase();
  if (ONLINE.has(s)) return "online";
  if (PROBLEM.has(s)) return "problem";
  if (s === "stopped" || s === "paused") return "stopped";
  return "unknown";
}

/** Zustand eines Servers samt An/Aus und „beim Anbieter nicht mehr vorhanden“ (beides grau, keine Störung). */
export function hostHealthOf(host: Pick<HostOut, "status" | "power_state" | "gone_since">): HostHealth {
  return host.gone_since ? "stopped" : hostHealth(host.status, host.power_state);
}

const STATUS_TEXT: Record<HostHealth, string> = { online: "läuft", stopped: "aus", problem: "nicht erreichbar", unknown: "unbekannt" };

/** Zustand eines Servers in Worten. Ein von Hand angelegter Server hat ohne „Verbindung prüfen“ nie
 * einen Zustand (nichts hat ihn je gesehen): das heißt „noch nicht geprüft“, nicht „unbekannt“. */
export function hostStatusText(host: Pick<HostOut, "status" | "last_seen_at" | "power_state" | "gone_since">): string {
  if (host.gone_since) return "nicht mehr vorhanden";
  const health = hostHealth(host.status, host.power_state);
  return health === "unknown" && !host.last_seen_at ? "noch nicht geprüft" : STATUS_TEXT[health];
}

export function isNode(host: HostOut): boolean {
  return host.kind === "hypervisor" || host.kind === "node";
}

export function hasConsole(host: HostOut): boolean {
  return host.kind === "vm" || host.kind === "lxc";
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 4) return `${(bytes / 1024 ** 4).toFixed(1)} TB`;
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

export function formatUptime(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return "–";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days} T ${hours} h`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

/** „vor 4 min“ aus einem Alter in Sekunden. */
export function formatAge(seconds: number): string {
  if (seconds < 60) return "gerade eben";
  if (seconds < 3600) return `vor ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `vor ${Math.floor(seconds / 3600)} h`;
  return `vor ${Math.floor(seconds / 86400)} T`;
}

export function relativeTime(iso: string, now = Date.now()): string {
  const diff = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (diff < 60) return "gerade eben";
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} h`;
  return `vor ${Math.floor(diff / 86400)} T`;
}

/** Stabile Farbe je Name -- App-Kacheln ohne eigenes Logo bleiben unterscheidbar. */
export function hueFor(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) % 360;
  return h;
}
