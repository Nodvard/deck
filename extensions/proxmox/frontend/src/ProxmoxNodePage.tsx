/**
 * Die "Node-Seite" -- PageSpec `component=
 * "ProxmoxNodePage"` (siehe nodvard_deck_ext_proxmox/__init__.py). Ruft ausschliesslich
 * bereits bestehende, generische KERN-Endpunkte auf (`GET /hosts`, `GET
 * /hosts/{id}/metrics`, `POST /hosts/{id}/actions/{type}`) -- kein einziger
 * proxmox-spezifischer Endpunkt fuer Liste/Metriken/Aktionen selbst, das ist die
 * eigentliche Nagelprobe: der Kern weiss nichts von "Proxmox", diese
 * Seite tut es, und beide passen trotzdem zusammen.
 *
 * Authentifizierung: ein Extension-Bundle hat keinen Zugriff auf state/auth.ts (nur
 * React/ReactDOM sind ueber den Import-Map-Shim geteilt) -- `window.__nodvardDeck.
 * getAccessToken()` (main.tsx) liefert den aktuellen Access-Token live. Anders
 * als die Kern-App selbst versucht diese Seite KEINEN stillen Refresh bei 401.
 *
 * Aufbau: oben Kacheln mit dem Wichtigsten (Knoten, Gäste, Updates, vollster Speicher), darunter
 * Reiter (`?tab=`): „VMs & Container“, „Speicher“, „Updates“, „Aufgabenverlauf“, „Verbindungen“.
 * Innerhalb der Reiter nach Verbindung (mehrere Proxmox-Instanzen) UND Knoten gruppiert
 * (`provider_ref = "<connection>/<kind>/<node>/<vmid>"`, siehe capabilities.py); jede VM/jeder
 * Container klappt auf und zeigt echte Live-Metriken (`GET /hosts/{id}/metrics`,
 * CPU/RAM/Laufzeit). In der Zeile stehen nur Konsole und Starten/Herunterfahren, der Rest
 * unter „Mehr“.
 *
 * **Freigabe:** `POST /hosts/{id}/actions/{type}` erzeugt bei `autonomy.mode=propose`
 * (Default) IMMER nur einen Vorschlag (`status=proposed`) -- eine zweite, separate
 * Bestaetigung ueber `POST /actions/{id}/approve` ist noetig (sonst auf der
 * Aktionen-Seite, siehe ActionsPage.tsx im Kern). Diese Seite bestaetigt einen eigenen
 * Vorschlag automatisch, WENN der angemeldete
 * Nutzer die noetige `actions.approve:<risk>`-Berechtigung hat (`window.__nodvardDeck.
 * hasPermission()`) -- der Bestaetigungsdialog VOR dem Vorschlag ist in diesem Fall
 * bereits die menschliche Bestaetigung, ein zweiter Seitenwechsel waere unnoetige
 * Reibung. Fehlt die Berechtigung, bleibt der Vorschlag bewusst stehen (Vier-Augen-
 * Faelle, andere Rollen) -- die Seite verweist dann auf die Aktionen-Seite.
 */
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";

import { ACTION_STATUS_LABEL, type ActionState, isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, authedFetchConfirmed, errorFromBody, errorText } from "../../../_shared/frontend/src/api";
import { formatBytes as formatSize } from "../../../_shared/frontend/src/format";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { MoreMenu } from "../../../_shared/frontend/src/menu";
import { Button, EmptyState } from "../../../_shared/frontend/src/ui";
import { FOCUS_CLASS, deck } from "../../../_shared/frontend/src/deck";

import { CertificateStatus, ConnectionCertificate } from "../../../_shared/frontend/src/certificate";

import { OldTokens } from "./OldTokens";
import { ProxmoxConnect } from "./ProxmoxConnect";

/** Wie lange nach dem Verbinden die Seite noch einmal nachlädt (das Einlesen läuft dann im Hintergrund). */
const RELOAD_AFTER_CONNECT_MS = 5000;

interface ConnectionOut {
  name: string;
  base_url: string;
  token_id: string;
  tls_insecure_skip_verify: boolean;
  /** Gemerkter Fingerabdruck des Zertifikats (`AB:CD:…`), sonst null. */
  tls_fingerprint_sha256?: string | null;
  tls_fingerprint_invalid?: boolean;
  enabled: boolean;
  has_token: boolean;
}

interface HostOut {
  id: string;
  name: string;
  display_name: string;
  status: string;
  kind: string | null;
  provider_ref: string | null;
}

interface MetricsOut {
  values: Record<string, number>;
  sampled_at: string;
}

/** Host-Status (HostStatus-Enum) auf Deutsch -- vorher stand "up"/"down" roh in der Tabelle. */
const HOST_STATUS_LABEL: Record<string, string> = { up: "läuft", down: "gestoppt", unknown: "unbekannt", maintenance: "Wartung" };

const KIND_LABEL: Record<string, string> = { vm: "VM", lxc: "LXC", hypervisor: "Knoten" };

/** `showWhen`: nur die Knoepfe, die im aktuellen Zustand Sinn ergeben (sonst stuende
 * z. B. "Starten" neben jeder laufenden VM). Unbekannter Zustand -> alle.
 * `vm.shutdown` fährt den Gast geordnet herunter und ist der normale Weg; `vm.stop` ist in
 * Proxmox ein hartes Ausschalten (/status/stop) -- ein Knopf, der nur "Stoppen"
 * hiesse, warnte nicht vor Datenverlust. Das harte Ausschalten steht deshalb ganz hinten
 * und in Rot (`variant: "danger"`), als Ausweg für einen hängenden Gast. */
const VM_ACTIONS: {
  type: string;
  label: string;
  confirm?: string;
  /** Die Rückfrage mit rotem Bestätigungsknopf (Standard: ja). */
  dangerConfirm?: boolean;
  variant?: "danger";
  showWhen?: (status: string) => boolean;
  /** Direkt in der Zeile; alle anderen stehen unter „Mehr“. */
  main?: boolean;
}[] = [
  { type: "vm.start", label: "Starten", showWhen: (s) => s !== "up", main: true },
  {
    type: "vm.shutdown",
    label: "Herunterfahren",
    confirm: "sauber herunterfahren? Das Betriebssystem in der VM fährt geordnet herunter.",
    dangerConfirm: false,
    showWhen: (s) => s !== "down",
    main: true,
  },
  { type: "vm.reboot", label: "Neustarten", confirm: "wirklich neu starten?", showWhen: (s) => s !== "down" },
  { type: "vm.snapshot", label: "Snapshot anlegen" },
  {
    type: "vm.stop",
    label: "Hart ausschalten",
    confirm: "wirklich hart ausschalten? Das ist wie Stecker ziehen: nicht gespeicherte Daten gehen verloren.",
    variant: "danger",
    showWhen: (s) => s !== "down",
  },
];

/** Deutsche Namen fuer die Meldung nach einer Aktion (statt des internen Namens,
 * z. B. "vm.snapshot_rollback"). */
const ACTION_LABEL: Record<string, string> = {
  ...Object.fromEntries(VM_ACTIONS.map((a) => [a.type, a.label])),
  "vm.snapshot_rollback": "Snapshot zurückrollen",
  "vm.snapshot_delete": "Snapshot löschen",
};

/** Name der Aktion in Meldungen; bei Snapshot-Aktionen mit dem Snapshot: `Snapshot löschen „a“`. */
function actionLabel(actionType: string, snapname?: string): string {
  const label = ACTION_LABEL[actionType] ?? actionType;
  return snapname ? `${label} „${snapname}“` : label;
}

/** "docker: Snapshot zurückrollen -> fehlgeschlagen: VM is locked (backup)" -- bei einem
 * Fehlschlag mit dem Grund aus `result.error`, statt nur "fehlgeschlagen"; läuft der Task in
 * Proxmox noch, mit dem Hinweis aus `result.output`. */
function describeOutcome(hostLabel: string, actionType: string, body: ActionState, snapname?: string): string {
  const label = actionLabel(actionType, snapname);
  const status = ACTION_STATUS_LABEL[body.status ?? ""] ?? body.status ?? "?";
  // "Herunterfahren angefordert, läuft nach 190s noch": die Aktion gilt als erledigt, der
  // Gast ist aber noch nicht aus -- das muss beim Nutzer ankommen, nicht nur "abgeschlossen".
  if (body.status === "succeeded" && body.result?.detail?.task_status === "running" && body.result.output?.trim()) {
    return `${hostLabel}: ${label} -> ${body.result.output.trim()}`;
  }
  const reason = body.status === "failed" ? body.result?.error?.trim() : undefined;
  return reason ? `${hostLabel}: ${label} -> ${status}: ${reason}` : `${hostLabel}: ${label} -> ${status}.`;
}

/** "<connection>/<kind>/<node>/<vmid>" -> {connection, node}; robust gegen
 * "<connection>/node/<node>" (kein vmid-Teil). */
function parseProviderRef(ref: string | null): { connection: string; node: string; vmid: string | null } {
  if (!ref) return { connection: "?", node: "?", vmid: null };
  // Knoten: "<verbindung>/node/<knoten>", Gast: "<verbindung>/<vm|lxc>/<knoten>/<vmid>"
  const parts = ref.split("/");
  return { connection: parts[0] ?? "?", node: parts[2] ?? "?", vmid: parts[3] ?? null };
}

/** Schluessel fuer Sperre und Meldung einer Aktion: `host:aktion`, bei Snapshots `host:aktion:snapshot`. */
function pendingKey(hostId: string, actionType: string, snapname?: string): string {
  return [hostId, actionType, snapname].filter(Boolean).join(":");
}

function scrollToElement(id: string): void {
  // jsdom kennt scrollIntoView nicht -- daher der optionale Aufruf.
  document.getElementById(id)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
}

/** Proxmox zeigt Groessen immer mit einer Nachkommastelle ("2.0 GB"). */
const formatBytes = (n: number): string => formatSize(n, { fixed: true });

function formatUptime(seconds: number): string {
  if (seconds <= 0) return "–";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

interface SnapshotOut {
  name: string;
  description: string;
  snaptime: number | null;
  parent: string | null;
  with_ram: boolean;
}

interface StoragePool {
  id: string;
  connection: string;
  node: string;
  storage: string;
  type: string;
  content_labels: string[];
  shared: boolean;
  total: number | null;
  used: number | null;
  used_percent: number | null;
  tone: string;
  volumes?: { volid: string; vmid: string; name: string; size: number | null }[];
  other?: Record<string, { label: string; count: number; size: number }>;
}

const BAR_TONE: Record<string, string> = {
  good: "bg-emerald-400",
  warn: "bg-amber-400",
  danger: "bg-red-400",
  neutral: "bg-white/40",
};

/**
 * Snapshots eines Gasts: anlegen, zurueckrollen und loeschen, ohne in die Proxmox-
 * Oberflaeche zu wechseln -- sonst sammeln sie sich unsichtbar an. Zurueckrollen und
 * Loeschen laufen als Aktion ueber das Gate (`vm.snapshot_rollback` hohes Risiko,
 * `vm.snapshot_delete`).
 */
function SnapshotsPanel({
  hostId,
  refreshKey,
  onAction,
  isPending,
}: {
  hostId: string;
  refreshKey: number;
  onAction: (actionType: string, snapname: string) => void;
  /** Laeuft diese Aktion fuer diesen Snapshot schon? Dann ist der Knopf gesperrt. */
  isPending: (actionType: string, snapname: string) => boolean;
}): JSX.Element {
  const [snaps, setSnaps] = useState<SnapshotOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/guests/${hostId}/snapshots`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<SnapshotOut[]>;
      })
      .then((rows) => !cancelled && setSnaps(rows))
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [hostId, refreshKey]);

  if (error) return <p className="text-xs text-red-400">Snapshots nicht abrufbar: {error}</p>;
  if (!snaps) return <p className="text-xs opacity-60">Lade Snapshots …</p>;
  if (snaps.length === 0) return <p className="text-xs opacity-60">Keine Snapshots.</p>;
  return (
    <ul className="flex flex-col gap-1 text-xs" data-testid={`snapshots-${hostId}`}>
      {snaps.map((snap) => (
        <li key={snap.name} className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{snap.name}</span>
          <span className="opacity-60">{snap.snaptime ? new Date(snap.snaptime * 1000).toLocaleString() : ""}</span>
          {snap.with_ram && <span className="rounded bg-white/10 px-1 text-[10px]">mit RAM</span>}
          {snap.description && <span className="opacity-60">-- {snap.description}</span>}
          <span className="ml-auto flex gap-1.5">
            <button type="button" disabled={isPending("vm.snapshot_rollback", snap.name)} onClick={() => onAction("vm.snapshot_rollback", snap.name)} className="rounded bg-amber-500/20 px-2 py-0.5 text-amber-300 hover:bg-amber-500/30 disabled:opacity-40">
              Zurückrollen
            </button>
            <button type="button" disabled={isPending("vm.snapshot_delete", snap.name)} onClick={() => onAction("vm.snapshot_delete", snap.name)} className="px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40">
              Löschen
            </button>
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Speicher-Uebersicht: Belegung je Pool UND welche Gast-Disks
 * darauf liegen -- in Proxmox muss man dafuer jeden Pool einzeln aufklappen.
 */
interface NodeUpdates {
  connection: string;
  node: string;
  error: string | null;
  count: number | null;
  summary: string;
  badge: string;
  tone: string;
  pve_version?: string | null;
  running_kernel?: string | null;
  reboot_pending?: boolean;
  last_check?: number | null;
  last_check_ok?: boolean | null;
  /** Ergebnis des letzten Prüflaufs von Proxmox im Klartext ("OK", "WARNINGS: 1", Fehlertext). */
  last_check_status?: string | null;
  last_check_age_s?: number | null;
  /** Der nächtliche Prüflauf von Proxmox ist deutlich überfällig (mehr als 36 Stunden). */
  last_check_stale?: boolean;
  packages: { package: string; title: string | null; old_version: string | null; version: string; new_package: boolean }[];
}

const BADGE_TONE: Record<string, string> = {
  good: "bg-emerald-500/20 text-emerald-300",
  warn: "bg-amber-500/20 text-amber-300",
  danger: "bg-red-500/20 text-red-300",
  neutral: "bg-white/10 opacity-70",
};

/** Ein Werkzeug des Servers aus `GET /hosts/{id}/tools` (Kern), nur die Felder, die hier zählen. */
interface HostTool {
  ext_id: string;
  id: string;
  href: string;
}

/**
 * Der Weg zu den Updates dieses Servers in Nodvard Shield, oder null. Die Adresse kommt vom Kern (Werkzeug „Sicherheit“
 * von Shield); fehlt es, ist Shield aus oder dem Nutzer fehlt das Recht, und es gibt keinen Link.
 */
export function shieldUpdatesHref(tools: HostTool[]): string | null {
  const tool = tools.find((t) => t.ext_id === "shield" && t.id === "security");
  if (!tool || !tool.href.startsWith("/") || tool.href.startsWith("//")) return null;
  const url = new URL(tool.href, "http://deck.invalid");
  url.searchParams.set("tab", "updates");
  return url.pathname + url.search;
}

/**
 * Paket-Updates je Knoten -- hier steht nur, WAS wartet und ob danach ein Neustart fällig ist. Eingespielt wird in
 * Nodvard Shield (Link je Knoten, wenn Shield an ist) oder in der Proxmox-Oberfläche.
 */
function UpdatesSection({ nodes, shieldHref }: { nodes: NodeUpdates[]; shieldHref?: (n: NodeUpdates) => string | undefined }): JSX.Element {
  return (
    <div>
      <ul className="space-y-2">
        {nodes.map((n) => {
          const href = !n.error && (n.count ?? 0) > 0 ? shieldHref?.(n) : undefined;
          return (
          <li key={n.node} className="text-sm" data-testid={`updates-${n.node}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{n.node}</span>
              <span className={`rounded px-1.5 py-0.5 text-xs ${BADGE_TONE[n.tone] ?? BADGE_TONE.neutral}`}>{n.badge}</span>
              <span className="opacity-80">{n.summary}</span>
              {href && (
                <a href={href} className="ml-auto text-xs underline underline-offset-2 hover:text-white" data-testid={`updates-shield-${n.node}`}>
                  In Shield einspielen
                </a>
              )}
            </div>
            {!n.error && (
              <p className="text-xs opacity-60">
                Proxmox {n.pve_version ?? "?"} · Kernel {n.running_kernel ?? "?"}
                {n.last_check ? ` · zuletzt geprüft ${new Date(n.last_check * 1000).toLocaleString()}` : " · noch kein Prüflauf von Proxmox gefunden"}
              </p>
            )}
            {!n.error && n.last_check_ok === false && (
              <p className="text-xs text-amber-300" data-testid={`updates-check-failed-${n.node}`}>
                Die letzte Prüfung auf neue Pakete ist fehlgeschlagen{n.last_check_status ? `: ${n.last_check_status}` : ""}. Die Liste kann veraltet sein.
              </p>
            )}
            {!n.error && n.last_check_stale && (
              <p className="text-xs text-amber-300" data-testid={`updates-check-stale-${n.node}`}>
                Der nächtliche Prüflauf von Proxmox ist seit {n.last_check_age_s ? `${Math.floor(n.last_check_age_s / 3600)} Stunden` : "über 36 Stunden"} nicht
                gelaufen. Die Liste kann veraltet sein; auf dem Knoten „systemctl status pve-daily-update.timer“ ansehen.
              </p>
            )}
            {n.packages.length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs opacity-70">Pakete anzeigen ({n.packages.length})</summary>
                <table className="mt-1 w-full text-xs">
                  <tbody className="divide-y divide-white/5">
                    {n.packages.map((pkg) => (
                      <tr key={pkg.package}>
                        <td className="py-0.5 pr-2 font-mono">{pkg.package}</td>
                        <td className="py-0.5 pr-2 whitespace-nowrap font-mono">
                          {pkg.new_package ? `neu: ${pkg.version}` : `${pkg.old_version} → ${pkg.version}`}
                        </td>
                        <td className="py-0.5 opacity-60">{pkg.title}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Fehler aus `/updates` bzw. `/storage` (Verbindung oder einzelner Knoten nicht erreichbar). */
interface FetchError {
  connection: string;
  node?: string;
  error: string;
}

/**
 * Ist eine Verbindung (oder ein Knoten) nicht erreichbar, sollen Updates und Speicher
 * nicht kommentarlos fehlen. Gleicher Fehler bei beiden -> eine Zeile.
 */
function UnreachableHint({
  connection, sources, testId = `unreachable-${connection}`,
}: {
  connection: string;
  sources: [string, FetchError[]][];
  testId?: string;
}): JSX.Element | null {
  const lines = new Map<string, string[]>();
  for (const [what, errors] of sources) {
    for (const e of errors) {
      if (e.connection !== connection) continue;
      const rest = `${e.node ? `von Knoten ${e.node} ` : ""}gerade nicht abrufbar: ${e.error}`;
      const whats = lines.get(rest) ?? [];
      if (!whats.includes(what)) whats.push(what);
      lines.set(rest, whats);
    }
  }
  if (lines.size === 0) return null;
  return (
    <div className="mt-3 space-y-1 text-sm text-amber-300" data-testid={testId}>
      {[...lines].map(([rest, whats]) => (
        <p key={rest}>{`${whats.join(" und ")} ${rest}`}</p>
      ))}
    </div>
  );
}

function StorageSection({ pools, focusNode = null }: { pools: StoragePool[]; focusNode?: string | null }): JSX.Element {
  return (
    <div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 text-left text-xs opacity-60">
            <th className="py-1 w-1/5">Pool</th>
            <th className="py-1">Inhalt</th>
            <th className="py-1 w-1/3">Belegung</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {pools.map((pool) => (
            <tr key={pool.id} data-testid={`pool-${pool.storage}`} className={focusNode && pool.node === focusNode ? FOCUS_CLASS : undefined}>
              <td className="py-1.5 align-top">
                <span className="font-medium">{pool.storage}</span>
                <span className="block text-xs opacity-60">
                  {pool.type}
                  {pool.shared ? " · geteilt" : ""}
                </span>
              </td>
              <td className="py-1.5 align-top text-xs">
                <span className="opacity-70">{pool.content_labels.join(", ")}</span>
                {(pool.volumes ?? []).length > 0 && (
                  <span className="block">
                    Gast-Disks:{" "}
                    {(pool.volumes ?? []).map((v) => `${v.name} (${formatBytes(v.size ?? 0)})`).join(", ")}
                  </span>
                )}
                {Object.values(pool.other ?? {}).length > 0 && (
                  <span className="block opacity-60">
                    {Object.values(pool.other ?? {})
                      .map((o) => `${o.count} ${o.label} (${formatBytes(o.size)})`)
                      .join(", ")}
                  </span>
                )}
              </td>
              <td className="py-1.5 align-top text-xs">
                <div className="h-1.5 w-full overflow-hidden rounded bg-white/10" role="progressbar" aria-valuenow={pool.used_percent ?? 0} aria-valuemin={0} aria-valuemax={100}>
                  <div className={`h-full ${BAR_TONE[pool.tone] ?? BAR_TONE.neutral}`} style={{ width: `${Math.min(100, pool.used_percent ?? 0)}%` }} />
                </div>
                <span className="opacity-70">
                  {formatBytes(pool.used ?? 0)} / {formatBytes(pool.total ?? 0)}
                  {pool.used_percent != null ? ` (${pool.used_percent} %)` : ""}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface TaskOut {
  connection: string;
  node: string;
  upid: string;
  type: string;
  type_label: string;
  guest_id: string | null;
  guest_name: string | null;
  user: string | null;
  status: string | null;
  running: boolean;
  ok: boolean | null;
  starttime: number | null;
  duration_s: number | null;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function TaskRow({ task }: { task: TaskOut }): JSX.Element {
  const [lines, setLines] = useState<string[] | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || lines) return;
    try {
      const res = await authedFetch(
        `/ext/proxmox/tasks/${encodeURIComponent(task.connection)}/${encodeURIComponent(task.node)}/log?upid=${encodeURIComponent(task.upid)}`,
      );
      if (!res.ok) throw new Error(await errorText(res));
      setLines(((await res.json()) as { lines: string[] }).lines);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const statusClass = task.running ? "text-amber-300" : task.ok ? "text-emerald-300" : "text-red-300";
  const statusText = task.running ? "läuft" : task.ok ? "OK" : task.status ?? "Fehler";
  return (
    <>
      <tr className="cursor-pointer hover:bg-white/5" onClick={() => void toggle()} data-testid={`task-${task.upid}`}>
        <td className="py-1 whitespace-nowrap opacity-70">{task.starttime ? new Date(task.starttime * 1000).toLocaleString() : ""}</td>
        <td className="py-1">
          {task.type_label}
          {task.guest_name ? ` · ${task.guest_name}` : task.guest_id ? ` · ${task.guest_id}` : ""}
        </td>
        <td className="py-1 opacity-70">{task.user}</td>
        <td className={`py-1 break-words ${statusClass}`}>{statusText}</td>
        <td className="py-1 whitespace-nowrap opacity-70">{formatDuration(task.duration_s)}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={5} className="bg-black/30 p-2">
            {error && <p className="text-red-400">{error}</p>}
            {!error && !lines && <p className="opacity-60">Lade Protokoll …</p>}
            {lines && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]">{lines.join("\n") || "(leer)"}</pre>}
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Aufgabenverlauf einer Verbindung: was auf den Knoten passiert ist, wer es ausgeloest hat, ob es
 * geklappt hat -- z. B. geplante Neustarts oder Backups, die sonst nur in der
 * Proxmox-Oberflaeche sichtbar waeren.
 */
function TasksTable({ tasks }: { tasks: TaskOut[] }): JSX.Element {
  if (tasks.length === 0) return <p className="text-xs opacity-60">Keine Aufgaben in letzter Zeit.</p>;
  // Am Handy ist die Tabelle breiter als der Bildschirm: sie scrollt für sich, die Seite bleibt stehen.
  return (
    <div className="overflow-x-auto">
    <table className="w-full min-w-[32rem] text-xs">
      <thead>
        <tr className="border-b border-white/10 text-left opacity-60">
          <th className="py-1">Zeit</th>
          <th className="py-1">Aufgabe</th>
          <th className="py-1">Benutzer</th>
          <th className="py-1">Status</th>
          <th className="py-1">Dauer</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-white/5">
        {tasks.map((task) => (
          <TaskRow key={task.upid} task={task} />
        ))}
      </tbody>
    </table>
    </div>
  );
}

interface GuestDisk {
  slot: string;
  kind: "disk" | "cdrom" | "cloudinit" | "bind" | "unused";
  storage: string | null;
  volume: string | null;
  size: string | null;
  mountpoint: string | null;
}

interface GuestNic {
  slot: string;
  name: string | null;
  model: string | null;
  mac: string | null;
  bridge: string | null;
  vlan: string | null;
  firewall: boolean;
  configured_ip: string | null;
  link_down: boolean;
  ips: string[];
}

interface GuestDetails {
  kind: "qemu" | "lxc";
  cores: number;
  sockets: number | null;
  cpu_type: string | null;
  memory_mb: number | null;
  balloon_mb: number | null;
  swap_mb: number | null;
  os: string | null;
  onboot: boolean;
  startup_order: number | null;
  disks: GuestDisk[];
  networks: GuestNic[];
  passthrough: string[];
  tags: string[];
  running: boolean;
  other_ips: string[];
  agent_enabled?: boolean;
  agent_responding?: boolean | null;
  bios?: string;
  machine?: string;
  unprivileged?: boolean;
  features?: string[];
}

function formatMb(mb: number | null): string {
  if (mb == null) return "?";
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;
}

function diskLabel(d: GuestDisk): string {
  switch (d.kind) {
    case "cdrom":
      return d.volume && d.volume !== "none" ? `CD/DVD: ${d.volume}` : "CD/DVD (leer)";
    case "cloudinit":
      return `Cloud-Init-Laufwerk auf ${d.storage}`;
    case "bind":
      return `Host-Ordner ${d.volume} → ${d.mountpoint}`;
    case "unused":
      return `Nicht zugeordnet: ${d.storage}:${d.volume}`;
    default:
      return `${d.size ?? "?"} auf ${d.storage ?? "?"}${d.mountpoint ? ` → ${d.mountpoint}` : ""}`;
  }
}

function agentText(d: GuestDetails): { text: string; className: string } {
  if (!d.agent_enabled) return { text: "nicht eingerichtet", className: "opacity-60" };
  if (!d.running) return { text: "eingerichtet (Gast aus)", className: "opacity-60" };
  if (d.agent_responding) return { text: "aktiv", className: "text-emerald-300" };
  return { text: "eingerichtet, antwortet nicht", className: "text-amber-300" };
}

type EditKey = "cores" | "sockets" | "memory" | "balloon" | "swap" | "onboot" | "startup_order";
type EditValues = Record<Exclude<EditKey, "onboot">, string> & { onboot: boolean };

const EDIT_KEYS: Record<GuestDetails["kind"], EditKey[]> = {
  qemu: ["cores", "sockets", "memory", "balloon", "onboot", "startup_order"],
  lxc: ["cores", "memory", "swap", "onboot", "startup_order"],
};
const EDIT_LABEL: Record<EditKey, string> = {
  cores: "Kerne", sockets: "Sockel", memory: "RAM (MB)", balloon: "Ballon-Minimum (MB)", swap: "Swap (MB)",
  onboot: "Autostart", startup_order: "Startreihenfolge",
};

function toEditValues(d: GuestDetails): EditValues {
  return {
    cores: String(d.cores), sockets: String(d.sockets ?? 1), memory: String(d.memory_mb ?? ""),
    // Kein Ballon-Wert heisst in Proxmox: Minimum = RAM.
    balloon: String(d.balloon_mb ?? d.memory_mb ?? ""), swap: String(d.swap_mb ?? 0),
    onboot: d.onboot, startup_order: d.startup_order == null ? "" : String(d.startup_order),
  };
}

/** Nur was sich wirklich geaendert hat -- der Server prueft Whitelist und Grenzen. */
export function editChanges(kind: GuestDetails["kind"], before: EditValues, after: EditValues): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const key of EDIT_KEYS[kind]) {
    if (before[key] === after[key]) continue;
    if (key === "onboot") changes.onboot = after.onboot;
    else if (key === "startup_order") changes.startup_order = after.startup_order === "" ? null : Number(after.startup_order);
    else changes[key] = Number(after[key]);
  }
  return changes;
}

/**
 * Hardware aendern (Roadmap Punkt 1): Kerne, RAM, Autostart ... ueber das Aktions-Gate
 * (`vm.config_set`). Disks, Netzwerk und Durchreichungen bleiben bewusst in Proxmox.
 */
function GuestEditForm({ hostId, details, onDone }: { hostId: string; details: GuestDetails; onDone: (message: string | null) => void }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const initial = toEditValues(details);
  const [values, setValues] = useState<EditValues>(initial);
  const [busy, setBusy] = useState(false);
  const changes = editChanges(details.kind, initial, values);
  const changed = Object.keys(changes);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (changed.length === 0) return;
    const summary = changed
      .map((k) => `${EDIT_LABEL[k as EditKey].replace(/ \(MB\)$/, "")}: ${k === "onboot" ? (initial.onboot ? "an" : "aus") : initial[k as Exclude<EditKey, "onboot">] || "keine"} → ${k === "onboot" ? (values.onboot ? "an" : "aus") : values[k as Exclude<EditKey, "onboot">] || "keine"}`)
      .join(", ");
    const ok = await deck().confirmDialog(`Hardware ändern – ${summary}? Manches greift erst nach einem Neustart des Gasts.`, { confirmLabel: "Ändern" });
    if (!ok) return;
    setBusy(true);
    try {
      const { action, approved } = await runAction(`/hosts/${hostId}/actions/vm.config_set`, {
        method: "POST",
        body: JSON.stringify({ payload: { changes }, reason: "Hardware über die Proxmox-Seite geändert." }),
      }, { signal: unmountSignal() });
      const status = action.status ?? "?";
      if (approved) {
        onDone(
          status === "succeeded" ? action.result?.output ?? "Geändert."
            : isActionRunning(status) ? RUNNING_IN_BACKGROUND
            : `Fehlgeschlagen: ${action.result?.error ?? status}`,
        );
      } else if (status === "proposed") {
        onDone(`Änderung vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
      } else {
        onDone(`${ACTION_STATUS_LABEL[status] ?? status}.`);
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void save(e)} className="mt-2 rounded border border-white/10 bg-black/20 p-2" data-testid={`edit-${hostId}`}>
      <div className="flex flex-wrap items-end gap-3">
        {EDIT_KEYS[details.kind].map((key) =>
          key === "onboot" ? (
            <label key={key} className="flex items-center gap-1 pb-1">
              <input type="checkbox" checked={values.onboot} onChange={(e) => setValues({ ...values, onboot: e.target.checked })} />
              {EDIT_LABEL[key]}
            </label>
          ) : (
            <label key={key} className="flex flex-col gap-0.5">
              <span className="opacity-60">{EDIT_LABEL[key]}</span>
              <input
                type="number"
                min={key === "startup_order" || key === "balloon" || key === "swap" ? 0 : 1}
                value={values[key]}
                placeholder={key === "startup_order" ? "keine" : undefined}
                onChange={(e) => setValues({ ...values, [key]: e.target.value })}
                className="w-28 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
              />
            </label>
          ),
        )}
        <button type="submit" disabled={busy || changed.length === 0} className="rounded bg-white/15 px-3 py-1 hover:bg-white/25 disabled:opacity-40">
          {busy ? "…" : "Speichern"}
        </button>
        <button type="button" onClick={() => onDone(null)} className="rounded px-2 py-1 opacity-70 hover:opacity-100">
          Abbrechen
        </button>
      </div>
      {details.kind === "qemu" && <p className="mt-1 opacity-50">Ballon-Minimum 0 schaltet Ballooning ab. Kerne und RAM greifen bei laufender VM meist erst nach einem Neustart.</p>}
    </form>
  );
}

/**
 * Hardware-Ueberblick eines Gasts -- was man sonst im
 * "Hardware"-Reiter nachsieht. Kerne/RAM/Autostart lassen sich hier aendern (ueber
 * das Gate); Disks, Netzwerk und Durchreichungen bleiben in Proxmox.
 */
function GuestDetailsPanel({ hostId }: { hostId: string }): JSX.Element {
  const [details, setDetails] = useState<GuestDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editMessage, setEditMessage] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/guests/${hostId}/details`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<GuestDetails>;
      })
      .then((data) => {
        if (!cancelled) setDetails(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [hostId, reloadKey]);

  if (error) return <p className="mt-2 text-xs text-red-400">Details nicht verfügbar: {error}</p>;
  if (!details) return <p className="mt-2 text-xs opacity-60">Lade Details …</p>;

  const d = details;
  const agent = d.kind === "qemu" ? agentText(d) : null;
  const cpu = d.kind === "qemu" ? `${d.cores * (d.sockets ?? 1)} vCPU${d.cpu_type ? ` (${d.cpu_type})` : ""}` : `${d.cores} Kerne`;
  const memory =
    d.kind === "qemu"
      ? `${formatMb(d.memory_mb)}${d.balloon_mb ? `, Ballooning ab ${formatMb(d.balloon_mb)}` : ""}`
      : `${formatMb(d.memory_mb)}${d.swap_mb ? ` + ${formatMb(d.swap_mb)} Swap` : ""}`;
  return (
    <div className="mt-2 text-xs" data-testid={`details-${hostId}`}>
      {deck().hasPermission("hosts.execute") && !editing && (
        <button type="button" onClick={() => { setEditMessage(null); setEditing(true); }} className="mb-2 px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
          Hardware ändern
        </button>
      )}
      {editing && (
        <GuestEditForm
          hostId={hostId}
          details={d}
          onDone={(message) => {
            setEditing(false);
            setEditMessage(message);
            if (message) setReloadKey((n) => n + 1);
          }}
        />
      )}
      {editMessage && <p className="mb-2 opacity-90" role="status">{editMessage}</p>}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 opacity-80 sm:grid-cols-4">
        <div>
          <dt className="opacity-60">Prozessor</dt>
          <dd>{cpu}</dd>
        </div>
        <div>
          <dt className="opacity-60">Arbeitsspeicher</dt>
          <dd>{memory}</dd>
        </div>
        <div>
          <dt className="opacity-60">System</dt>
          <dd>
            {d.os ?? "?"}
            {d.kind === "qemu" ? ` · ${d.bios} · ${d.machine}` : d.unprivileged ? " · unprivilegiert" : " · privilegiert"}
          </dd>
        </div>
        <div>
          <dt className="opacity-60">Autostart</dt>
          <dd>{d.onboot ? `ja${d.startup_order != null ? ` (Reihenfolge ${d.startup_order})` : ""}` : "nein"}</dd>
        </div>
        {agent && (
          <div>
            <dt className="opacity-60">Gast-Agent</dt>
            <dd className={agent.className}>{agent.text}</dd>
          </div>
        )}
        {d.features && d.features.length > 0 && (
          <div>
            <dt className="opacity-60">Funktionen</dt>
            <dd>{d.features.join(", ")}</dd>
          </div>
        )}
        {d.passthrough.length > 0 && (
          <div>
            <dt className="opacity-60">Durchgereicht</dt>
            <dd>{d.passthrough.join(", ")}</dd>
          </div>
        )}
        {d.tags.length > 0 && (
          <div>
            <dt className="opacity-60">Tags</dt>
            <dd>{d.tags.join(", ")}</dd>
          </div>
        )}
      </dl>
      <p className="mt-2 mb-1 font-medium opacity-60">Laufwerke</p>
      <ul className="space-y-0.5">
        {d.disks.map((disk) => (
          <li key={disk.slot} className={disk.kind === "unused" ? "text-amber-300" : "opacity-80"}>
            <span className="font-mono opacity-60">{disk.slot}</span> {diskLabel(disk)}
          </li>
        ))}
      </ul>
      <p className="mt-2 mb-1 font-medium opacity-60">Netzwerk</p>
      <ul className="space-y-0.5">
        {d.networks.map((nic) => (
          <li key={nic.slot} className="opacity-80">
            <span className="font-mono opacity-60">{nic.slot}</span> {nic.name ? `${nic.name} · ` : ""}
            {nic.bridge ?? "?"}
            {nic.vlan ? ` (VLAN ${nic.vlan})` : ""} · {nic.model ?? "?"} · <span className="font-mono">{nic.mac ?? "?"}</span>
            {nic.firewall ? " · Firewall" : ""}
            {nic.link_down ? " · getrennt" : ""}
            {nic.ips.length > 0
              ? ` · ${nic.ips.join(", ")}`
              : nic.configured_ip
                ? ` · ${nic.configured_ip === "dhcp" ? "DHCP" : nic.configured_ip}`
                : ""}
          </li>
        ))}
        {d.other_ips.length > 0 && <li className="opacity-60">Weitere Adressen im Gast: {d.other_ips.join(", ")}</li>}
      </ul>
    </div>
  );
}

interface DiskHealth {
  devpath: string;
  model: string;
  summary: string;
  badge: string;
  tone: string;
  power_on_hours: number | null;
}

interface NodeHealth {
  cpu_model: string | null;
  cpu_cores: number | null;
  cpu_threads: number | null;
  loadavg: number[];
  io_wait_percent: number;
  mem_total: number | null;
  mem_available: number | null;
  swap_total: number | null;
  swap_used: number | null;
  rootfs_total: number | null;
  rootfs_used: number | null;
  ksm_shared: number | null;
  uptime_s: number | null;
  pve_version: string | null;
  kernel: string | null;
  boot_mode: string | null;
  disks: DiskHealth[];
  disks_error: string | null;
  /** ZFS-Pools des Knotens (fehlt bei älteren Servern). */
  zfs?: ZfsPool[];
  zfs_error?: string | null;
}

interface ZfsPool {
  name: string;
  badge: string;
  tone: string;
  summary: string;
}

/**
 * Knoten-Gesundheit: "Uebersicht" + "Disks" des Knotens. Nur lesend. Hat ein Knoten
 * nur eine Platte, ist sie ein Single Point of Failure -- der SMART-Zustand steht
 * deshalb hier UND im Widget "Proxmox-Datentraeger".
 */
function NodeHealthPanel({ hostId }: { hostId: string }): JSX.Element {
  const [health, setHealth] = useState<NodeHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/nodes/${hostId}/health`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<NodeHealth>;
      })
      .then((data) => {
        if (!cancelled) setHealth(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [hostId]);

  if (error) return <p className="mt-2 text-xs text-red-400">Knotendaten nicht verfügbar: {error}</p>;
  if (!health) return <p className="mt-2 text-xs opacity-60">Lade Knotendaten …</p>;

  const h = health;
  const memUsed = h.mem_total != null && h.mem_available != null ? h.mem_total - h.mem_available : null;
  return (
    <div className="mt-2 text-xs" data-testid={`node-health-${hostId}`}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 opacity-80 sm:grid-cols-4">
        <div className="col-span-2">
          <dt className="opacity-60">Prozessor</dt>
          <dd>
            {h.cpu_model ?? "?"} ({h.cpu_cores ?? "?"} Kerne / {h.cpu_threads ?? "?"} Threads)
          </dd>
        </div>
        <div>
          <dt className="opacity-60">Last (1/5/15 min)</dt>
          <dd>
            {h.loadavg.map((l) => l.toFixed(2)).join(" / ")} · IO-Wartezeit {h.io_wait_percent} %
          </dd>
        </div>
        <div>
          <dt className="opacity-60">Läuft seit</dt>
          <dd>{formatUptime(h.uptime_s ?? 0)}</dd>
        </div>
        <div>
          <dt className="opacity-60">RAM belegt</dt>
          <dd>
            {memUsed != null ? formatBytes(memUsed) : "?"} / {formatBytes(h.mem_total ?? 0)}
            {h.ksm_shared ? ` · KSM teilt ${formatBytes(h.ksm_shared)}` : ""}
          </dd>
        </div>
        <div>
          <dt className="opacity-60">Swap belegt</dt>
          <dd>
            {formatBytes(h.swap_used ?? 0)} / {formatBytes(h.swap_total ?? 0)}
          </dd>
        </div>
        <div>
          <dt className="opacity-60">Systemplatte</dt>
          <dd>
            {formatBytes(h.rootfs_used ?? 0)} / {formatBytes(h.rootfs_total ?? 0)}
          </dd>
        </div>
        <div>
          <dt className="opacity-60">System</dt>
          <dd>
            Proxmox {h.pve_version ?? "?"} · Kernel {h.kernel ?? "?"}
            {h.boot_mode ? ` · ${h.boot_mode}` : ""}
          </dd>
        </div>
      </dl>
      <p className="mt-2 mb-1 font-medium opacity-60">Datenträger</p>
      {h.disks_error && <p className="text-red-400">Nicht abrufbar: {h.disks_error}</p>}
      <ul className="space-y-0.5">
        {h.disks.map((d) => (
          <li key={d.devpath} className="flex flex-wrap items-center gap-2 opacity-90">
            <span className={`rounded px-1.5 py-0.5 ${BADGE_TONE[d.tone] ?? BADGE_TONE.neutral}`}>{d.badge}</span>
            <span>{d.model}</span>
            <span className="opacity-60">
              {d.summary}
              {d.power_on_hours != null ? ` · ${d.power_on_hours.toLocaleString()} Betriebsstunden` : ""}
            </span>
          </li>
        ))}
      </ul>
      {((h.zfs?.length ?? 0) > 0 || h.zfs_error) && (
        <>
          <p className="mt-2 mb-1 font-medium opacity-60">ZFS-Pools</p>
          {h.zfs_error && <p className="text-red-400">Nicht abrufbar: {h.zfs_error}</p>}
          <ul className="space-y-0.5" data-testid="zfs-pools">
            {(h.zfs ?? []).map((p) => (
              <li key={p.name} className="flex flex-wrap items-center gap-2 opacity-90">
                <span className={`rounded px-1.5 py-0.5 ${BADGE_TONE[p.tone] ?? BADGE_TONE.neutral}`}>{p.badge}</span>
                <span>{p.name}</span>
                <span className="opacity-60">{p.summary}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** Knoten-Karte: zugeklappt nur Name + Status; aufgeklappt Hardware + Datentraeger
 * (erst dann geladen -- SMART-Abfragen kosten auf pve2 spuerbar Zeit). */
function NodeCard({
  node, focused = false, visit = 0, updates, onOpenUpdates,
}: {
  node: HostOut;
  focused?: boolean;
  visit?: number;
  /** Updates dieses Knotens (fehlt, solange sie nicht geladen sind). */
  updates?: NodeUpdates;
  onOpenUpdates?: () => void;
}): JSX.Element {
  const [open, setOpen] = useState(focused);
  // Jeder Link auf diesen Knoten (auch derselbe noch einmal) klappt ihn wieder auf.
  useEffect(() => {
    if (focused) setOpen(true);
  }, [focused, visit]);
  return (
    <div id={`host-${node.id}`} className={`mb-2 rounded border border-white/10 p-2 text-sm ${focused ? FOCUS_CLASS : ""}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Knotendetails einklappen" : "Knotendetails anzeigen"}
        className="mr-2 opacity-60 hover:opacity-100"
      >
        {open ? "▾" : "▸"}
      </button>
      <span className="font-medium">{node.display_name}</span>{" "}
      <span className="opacity-60">(Knoten) – {HOST_STATUS_LABEL[node.status] ?? node.status}</span>
      {updates && !updates.error && updates.tone !== "good" && (
        <button
          type="button"
          onClick={onOpenUpdates}
          title="Zu den Updates"
          className={`ml-2 rounded px-1.5 py-0.5 text-xs hover:brightness-125 ${BADGE_TONE[updates.tone] ?? BADGE_TONE.neutral}`}
        >
          {updates.badge}
        </button>
      )}
      {open && <NodeHealthPanel hostId={node.id} />}
    </div>
  );
}

function MetricsPanel({ hostId }: { hostId: string }): JSX.Element {
  const [metrics, setMetrics] = useState<MetricsOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authedFetch(`/hosts/${hostId}/metrics`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<MetricsOut>;
      })
      .then((data) => {
        if (!cancelled) setMetrics(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [hostId]);

  if (error) return <p className="text-xs text-red-400">Metriken nicht verfügbar: {error}</p>;
  if (!metrics) return <p className="text-xs opacity-60">Lade Metriken …</p>;

  const v = metrics.values;
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs opacity-80 sm:grid-cols-4">
      <div>
        <dt className="opacity-60">CPU</dt>
        <dd>{v.cpu_percent?.toFixed(1) ?? "?"} %</dd>
      </div>
      <div>
        <dt className="opacity-60">RAM</dt>
        <dd>
          {formatBytes(v.mem_used_bytes ?? 0)} / {formatBytes(v.mem_total_bytes ?? 0)}
        </dd>
      </div>
      <div>
        <dt className="opacity-60">Laufzeit</dt>
        <dd>{formatUptime(v.uptime_s ?? 0)}</dd>
      </div>
      <div>
        <dt className="opacity-60">Stand</dt>
        <dd>{new Date(metrics.sampled_at).toLocaleTimeString()}</dd>
      </div>
    </dl>
  );
}

/**
 * Verbindungsverwaltung: Verbindungen (pve1, pve2, ...) lassen sich ueber die
 * Oberflaeche anlegen, aendern und entfernen. Ruft die
 * `GET/POST/PUT/DELETE /ext/proxmox/connections...`-
 * Endpunkte auf; der Token-WERT geht weiterhin ausschliesslich ueber den
 * bestehenden `POST .../{name}/token`-Endpunkt (Vault), nie durch dieses Formular.
 * "Aktiv"-Umschalter deckt das unabhaengige Aktivieren/Deaktivieren einzelner
 * Verbindungen ab (`enabled`-Feld, siehe config.py::build_connectors()).
 */
function ConnectionsPanel({
  onConnectWithPassword, version = 0, bare = false,
}: {
  /** „Mit Passwort verbinden“ öffnen; ohne die Angabe gibt es den Knopf nicht. */
  onConnectWithPassword?: () => void;
  /** Hochzählen lädt die Liste neu (z. B. nachdem „Mit Passwort verbinden“ eine Verbindung eingetragen hat). */
  version?: number;
  /** Im Reiter „Verbindungen“: gleich offen, ohne Aufklappen. */
  bare?: boolean;
}): JSX.Element {
  const [connections, setConnections] = useState<ConnectionOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBaseUrl, setNewBaseUrl] = useState("");
  const [newTokenId, setNewTokenId] = useState("");
  const [newTlsInsecure, setNewTlsInsecure] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [oldTokensFor, setOldTokensFor] = useState<string | null>(null);
  const [certificateFor, setCertificateFor] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    authedFetch("/ext/proxmox/connections")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ConnectionOut[]>;
      })
      .then(setConnections)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load, version]);

  async function addConnection(e: FormEvent) {
    e.preventDefault();
    setPending("add");
    setMessage(null);
    try {
      const res = await authedFetch("/ext/proxmox/connections", {
        method: "POST",
        body: JSON.stringify({
          name: newName, base_url: newBaseUrl, token_id: newTokenId, tls_insecure_skip_verify: newTlsInsecure,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setMessage(`Verbindung "${newName}" angelegt – jetzt noch ein Token setzen.`);
      setNewName("");
      setNewBaseUrl("");
      setNewTokenId("");
      setNewTlsInsecure(false);
      setShowAddForm(false);
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function setToken(name: string, replacing: boolean) {
    const value = await deck().promptDialog(
      replacing ? `Neues API-Token für "${name}" (ersetzt das gespeicherte, Klartext, nur hier eingeben):` : `API-Token für "${name}" (Klartext, nur hier eingeben):`,
    );
    if (!value) return;
    setPending(`token:${name}`);
    setMessage(null);
    try {
      // Legt an ODER ersetzt. Der alte Weg (`POST /ext/proxmox/connections/{name}/token`) legt nur
      // an und meldete beim zweiten Mal 409.
      // Ein gespeichertes Token ersetzt der Kern nur mit Passwort und gegebenenfalls Code (Dialog des Kerns).
      const res = await authedFetchConfirmed(`/extensions/proxmox/secrets`, (confirmation) => ({
        method: "PUT", body: JSON.stringify({ label: `proxmox-token:${name}`, value, ...confirmation }),
      }));
      if (res === null) return;
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      setMessage(`Token für "${name}" gesetzt.`);
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function toggleEnabled(conn: ConnectionOut) {
    setPending(`toggle:${conn.name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/proxmox/connections/${conn.name}`, {
        method: "PUT", body: JSON.stringify({ enabled: !conn.enabled }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function removeConnection(name: string) {
    const ok = await deck().confirmDialog(
      `Verbindung "${name}" wirklich entfernen? Das gesetzte Token wird mit gelöscht.`,
      { danger: true, confirmLabel: "Entfernen" },
    );
    if (!ok) return;
    setPending(`remove:${name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/proxmox/connections/${name}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  if (error) return <p className="mb-4 text-xs text-red-400">Verbindungen nicht ladbar: {error}</p>;

  const content = (
      <div className={bare ? "" : "mt-3"}>
        {message && <p className="mb-2 text-xs opacity-80">{message}</p>}
        {connections && connections.length === 0 && (
          <p className="mb-2 text-xs opacity-60">Noch keine Verbindung konfiguriert.</p>
        )}
        {connections && connections.length > 0 && (
          <table className="mb-3 w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 text-left opacity-60">
                <th className="py-1 w-1/6">Name</th>
                <th className="py-1 w-1/3">Adresse</th>
                <th className="py-1 whitespace-nowrap">Zertifikat</th>
                <th className="py-1 whitespace-nowrap">Token</th>
                <th className="py-1 whitespace-nowrap">Aktiv</th>
                <th className="py-1 whitespace-nowrap">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {connections.map((c) => (
                <tr key={c.name}>
                  <td className="py-1.5 break-words">{c.name}</td>
                  <td className="py-1.5 break-all opacity-70">{c.base_url}</td>
                  <td className="py-1.5 whitespace-nowrap"><CertificateStatus connection={c} /></td>
                  <td className="py-1.5 whitespace-nowrap">{c.has_token ? "gesetzt" : <span className="text-amber-400">fehlt</span>}</td>
                  <td className="py-1.5">
                    <button
                      type="button"
                      disabled={pending === `toggle:${c.name}`}
                      onClick={() => void toggleEnabled(c)}
                      className="px-2 py-0.5 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                    >
                      {c.enabled ? "aktiv" : "deaktiviert"}
                    </button>
                  </td>
                  <td className="py-1.5">
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        disabled={pending === `token:${c.name}`}
                        onClick={() => void setToken(c.name, c.has_token)}
                        className="px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                      >
                        {c.has_token ? "Token ersetzen" : "Token setzen"}
                      </button>
                      <button
                        type="button"
                        aria-pressed={certificateFor === c.name}
                        onClick={() => setCertificateFor(certificateFor === c.name ? null : c.name)}
                        className="px-2 py-1 whitespace-nowrap border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                      >
                        Zertifikat
                      </button>
                      <button
                        type="button"
                        aria-pressed={oldTokensFor === c.name}
                        onClick={() => setOldTokensFor(oldTokensFor === c.name ? null : c.name)}
                        className="px-2 py-1 whitespace-nowrap border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                      >
                        Alte Tokens
                      </button>
                      <button
                        type="button"
                        disabled={pending === `remove:${c.name}`}
                        onClick={() => void removeConnection(c.name)}
                        className="px-2 py-1 disabled:opacity-40 border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25 rounded-lg"
                      >
                        Entfernen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {certificateFor && connections?.some((c) => c.name === certificateFor) && (
          <ConnectionCertificate
            extId="proxmox" connection={certificateFor} onSaved={load}
            unchecked={connections?.find((c) => c.name === certificateFor)?.tls_insecure_skip_verify ?? false}
          />
        )}
        {oldTokensFor && connections?.some((c) => c.name === oldTokensFor) && <OldTokens connection={oldTokensFor} />}

        {!showAddForm && (
          <div className="flex flex-wrap gap-2">
            {onConnectWithPassword && (
              <button
                type="button"
                onClick={onConnectWithPassword}
                className="px-2 py-1 text-xs accent-gradient text-on-accent rounded-lg hover:bg-accent-strong font-medium"
              >
                Mit Passwort verbinden
              </button>
            )}
            <button
              type="button"
              onClick={() => setShowAddForm(true)}
              className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
            >
              + Neue Verbindung
            </button>
          </div>
        )}
        {showAddForm && (
          <form onSubmit={(e) => void addConnection(e)} className="flex flex-wrap items-end gap-2 text-xs">
            <label className="flex flex-col gap-1">
              Name
              <input required value={newName} onChange={(e) => setNewName(e.target.value)} className="px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" />
            </label>
            <label className="flex flex-col gap-1">
              Adresse (URL)
              <input
                required value={newBaseUrl} onChange={(e) => setNewBaseUrl(e.target.value)}
                placeholder="https://192.168.2.x:8006" className="w-56 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
              />
            </label>
            <label className="flex flex-col gap-1">
              Token-ID
              <input
                required value={newTokenId} onChange={(e) => setNewTokenId(e.target.value)}
                placeholder="root@pam!dashboard" className="w-40 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
              />
            </label>
            <label className="flex items-center gap-1" title="Veraltet: besser nach dem Anlegen unter „Zertifikat“ den Fingerabdruck merken.">
              <input type="checkbox" checked={newTlsInsecure} onChange={(e) => setNewTlsInsecure(e.target.checked)} />
              Zertifikat nicht prüfen (veraltet)
            </label>
            <button type="submit" disabled={pending === "add"} className="px-2 py-1 disabled:opacity-40 accent-gradient text-on-accent rounded-lg hover:bg-accent-strong font-medium">
              Anlegen
            </button>
            <button type="button" onClick={() => setShowAddForm(false)} className="px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
              Abbrechen
            </button>
          </form>
        )}
      </div>
  );
  // Gleiche Überschrift wie zugeklappt: Meldungen und Doku verweisen auf „Verbindungen verwalten“.
  if (bare) {
    return (
      <div className="mb-6 p-3 panel">
        <h3 className="mb-3 text-sm font-medium">Verbindungen verwalten</h3>
        {content}
      </div>
    );
  }
  return (
    <details className="mb-6 p-3 panel" open={connections?.length === 0}>
      <summary className="cursor-pointer text-sm font-medium">Verbindungen verwalten</summary>
      {content}
    </details>
  );
}

export function ProxmoxNodePage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  // Sprungziel von der Server-Seite des Kerns (`HostToolSpec.path` mit `?host=`, Aufgabenverlauf
  // zusaetzlich `&tasks=1`). Folgt der Adresszeile (location.ts): jeder Link, auch derselbe noch
  // einmal auf die schon offene Seite, markiert, klappt auf und scrollt hin (`visits`).
  const [urlParams, updateUrl, visits] = useUrlParams();
  const focusHost = urlParams.get("host") || null;
  const focusTasks = urlParams.get("tasks") === "1";
  // Reiter: `?tab=`; ein Link auf den Aufgabenverlauf (`&tasks=1`) ohne Reiter öffnet den Verlauf.
  const tabParam = urlParams.get("tab");
  const tab: TabId = isTab(tabParam) ? tabParam : focusTasks ? "tasks" : "guests";
  // Ein Wechsel per Hand beendet den Sprung von einer anderen Seite: sonst markierte z. B. „VMs & Container“
  // nach der Speicher-Meldung noch den Knoten und klappte ihn (mit SMART-Abfrage) auf.
  const setTab = useCallback((id: TabId) => updateUrl({ tab: id, host: null, tasks: null }), [updateUrl]);
  const [hosts, setHosts] = useState<HostOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Mehrere Aktionen koennen gleichzeitig laufen (z. B. mehrere Gaeste herunterfahren, das
  // dauert bis zu ~190 s): jeder Knopf bleibt bis zum ENDE SEINER Aktion gesperrt, und
  // jede Aktion behaelt ihre eigene Meldung. Eine Meldung entsteht
  // erst, wenn ihre Aktion fertig ist -- beginnt eine neue, sind alle vorhandenen also von
  // fertigen Aktionen und werden weggeraeumt (wie frueher die eine Meldung).
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  const [actionMessages, setActionMessages] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [expanded, setExpanded] = useState<string | null>(focusHost);
  const expandedFor = useRef(visits);
  useEffect(() => {
    if (expandedFor.current === visits) return;
    expandedFor.current = visits;
    setExpanded(focusHost);
  }, [visits, focusHost]);
  // Filter des Aufgabenverlaufs; entfernen nimmt nur `tasks` aus der Adresse.
  const taskFocus = focusTasks ? focusHost : null;
  const [snapshotRefresh, setSnapshotRefresh] = useState(0);
  const [storage, setStorage] = useState<StoragePool[] | null>(null);
  const [tasks, setTasks] = useState<TaskOut[] | null>(null);
  const [updates, setUpdates] = useState<NodeUpdates[] | null>(null);
  const [updatesErrors, setUpdatesErrors] = useState<FetchError[]>([]);
  const [storageErrors, setStorageErrors] = useState<FetchError[]>([]);
  // Ganze Abfrage gescheitert (nicht nur ein Knoten): sonst fehlten Updates und Speicher still, und der Aufgabenverlauf
  // stuende auf "(0)" -- als gaebe es nichts.
  const [updatesLoadError, setUpdatesLoadError] = useState<string | null>(null);
  const [tasksLoadError, setTasksLoadError] = useState<string | null>(null);
  const [storageLoadError, setStorageLoadError] = useState<string | null>(null);
  // „Mit Passwort verbinden“ schreibt Verbindung und Token wie die Verbindungsverwaltung (gleiches Recht).
  const canConnect = deck().hasPermission("secrets.write");
  const [connectOpen, setConnectOpen] = useState(false);
  const [connectionsVersion, setConnectionsVersion] = useState(0);
  const [connectedAt, setConnectedAt] = useState(0);

  const loadUpdates = useCallback(() => {
    setUpdatesLoadError(null);
    fetchJson<{ nodes: NodeUpdates[]; errors?: FetchError[] }>("/ext/proxmox/updates")
      .then((body) => {
        setUpdates(body.nodes ?? []);
        setUpdatesErrors(body.errors ?? []);
      })
      .catch((err: unknown) => setUpdatesLoadError(errorMessage(err)));
  }, []);
  useEffect(() => {
    loadUpdates();
  }, [loadUpdates]);
  const [showConsoleTasks, setShowConsoleTasks] = useState(false);

  const loadTasks = useCallback(() => {
    setTasksLoadError(null);
    fetchJson<{ tasks: TaskOut[] }>(`/ext/proxmox/tasks?limit=100${showConsoleTasks ? "&include_console=true" : ""}`)
      .then((body) => setTasks(body.tasks ?? []))
      .catch((err: unknown) => setTasksLoadError(errorMessage(err)));
  }, [showConsoleTasks]);
  useEffect(() => {
    loadTasks();
  }, [loadTasks, snapshotRefresh]);

  const loadStorage = useCallback(() => {
    setStorageLoadError(null);
    fetchJson<{ pools: StoragePool[]; errors?: FetchError[] }>("/ext/proxmox/storage")
      .then((body) => {
        setStorage(body.pools ?? []);
        setStorageErrors(body.errors ?? []);
      })
      .catch((err: unknown) => setStorageLoadError(errorMessage(err)));
  }, []);
  useEffect(() => {
    loadStorage();
  }, [loadStorage]);

  const load = useCallback(() => {
    setError(null);
    fetchJson<HostOut[]>("/hosts?tag=proxmox")
      .then(setHosts)
      .catch((err: unknown) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Nach „Mit Passwort verbinden“ liest Nodvard Deck im Hintergrund ein: nach ein paar Sekunden noch einmal nachsehen.
  useEffect(() => {
    if (!connectedAt) return undefined;
    const timer = window.setTimeout(load, RELOAD_AFTER_CONNECT_MS);
    return () => window.clearTimeout(timer);
  }, [connectedAt, load]);

  // Einmal je Link hinscrollen, sobald die Zeile existiert -- nicht bei jedem Neuladen.
  const scrolledFor = useRef<number | null>(null);
  useEffect(() => {
    if (!hosts || !focusHost || scrolledFor.current === visits) return;
    const target = hosts.find((h) => h.id === focusHost);
    if (!target) return;
    if (tab === "tasks" && !tasks) return;
    if (tab === "storage" && !storage) return;
    scrolledFor.current = visits;
    const { connection } = parseProviderRef(target.provider_ref);
    scrollToElement(tab === "tasks" ? `tasks-${connection}` : tab === "storage" ? `storage-${connection}` : `host-${target.id}`);
  }, [hosts, tasks, storage, focusHost, tab, visits]);

  // „In Shield einspielen“ je Knoten: erst beim Öffnen des Reiters „Updates“ fragt die Seite den Kern nach den Werkzeugen
  // jedes Knotens. Ohne Antwort (Fehler, Shield aus, kein Recht) gibt es einfach keinen Link.
  const [shieldLinks, setShieldLinks] = useState<Record<string, string>>({});
  const nodeIds = (hosts ?? []).filter((h) => h.kind === "hypervisor" || h.kind === "node").map((h) => h.id).join(",");
  useEffect(() => {
    if (tab !== "updates" || !nodeIds) return;
    let cancelled = false;
    void Promise.all(nodeIds.split(",").map(async (id) => {
      try {
        return [id, shieldUpdatesHref(await fetchJson<HostTool[]>(`/hosts/${encodeURIComponent(id)}/tools`))] as const;
      } catch {
        return [id, null] as const;
      }
    })).then((pairs) => {
      if (cancelled) return;
      setShieldLinks(Object.fromEntries(pairs.filter((p): p is readonly [string, string] => p[1] !== null)));
    });
    return () => {
      cancelled = true;
    };
  }, [tab, nodeIds]);

  async function trigger(
    hostId: string,
    hostLabel: string,
    actionType: string,
    confirmText?: string,
    payload: Record<string, unknown> = {},
    dangerConfirm = true,
  ) {
    if (confirmText) {
      const ok = await deck().confirmDialog(`"${hostLabel}" ${confirmText}`, { danger: dangerConfirm });
      if (!ok) return;
    }
    // Snapshot-Aktionen desselben Gasts laufen nebeneinander: der Snapshot gehoert in den
    // Schluessel, sonst ueberschreiben sich Meldung und Sperre gegenseitig.
    const snapname = typeof payload.snapname === "string" && payload.snapname ? payload.snapname : undefined;
    const key = pendingKey(hostId, actionType, snapname);
    setPending((prev) => new Set(prev).add(key));
    // Die Meldungen hier gehoeren zu fertigen Aktionen (laufende melden sich erst am Ende).
    setActionMessages(new Map());
    try {
      const { action, approved } = await runAction(`/hosts/${hostId}/actions/${actionType}`, {
        method: "POST",
        body: JSON.stringify({ payload, reason: `Über die Proxmox-Node-Seite ausgelöst (${actionType}).` }),
      }, { signal: unmountSignal() });

      if (!approved && action.status === "proposed") {
        setMessage(key, `${hostLabel}: ${actionLabel(actionType, snapname)} vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
      } else {
        setMessage(key, describeOutcome(hostLabel, actionType, action, snapname));
      }
      load();
      setSnapshotRefresh((n) => n + 1);
    } catch (err) {
      setMessage(key, `${hostLabel}: Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }

  /** Meldung einer Aktion (`hostId:aktion`) setzen. Die neueste steht unten. */
  function setMessage(key: string, text: string) {
    setActionMessages((prev) => {
      const next = new Map(prev);
      next.delete(key);
      next.set(key, text);
      return next;
    });
  }

  if (!hosts && !error) return <div className="p-6 text-sm opacity-60">Lade …</div>;
  if (error) {
    return (
      <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
        <LoadError what="Die Liste der Proxmox-Server" error={error} onRetry={load} />
      </div>
    );
  }

  // Der Abgleich legt Knoten als kind="hypervisor" an -- ein Filter nur auf "node"
  // zeigte deshalb nie einen Knoten an.
  const nodes = (hosts ?? []).filter((h) => h.kind === "hypervisor" || h.kind === "node");
  const guests = (hosts ?? []).filter((h) => h.kind === "vm" || h.kind === "lxc");
  const connections = [...new Set((hosts ?? []).map((h) => parseProviderRef(h.provider_ref).connection))].sort();
  const empty = !hosts || hosts.length === 0;

  // Aufgabenverlauf nur fuer den Host, von dessen Server-Seite der Nutzer kommt.
  const taskHost = (hosts ?? []).find((h) => h.id === taskFocus) ?? null;
  const taskRef = taskHost ? parseProviderRef(taskHost.provider_ref) : null;
  function tasksFor(connection: string): TaskOut[] {
    const all = (tasks ?? []).filter((t) => t.connection === connection);
    if (!taskRef || taskRef.connection !== connection) return all;
    return all.filter((t) => t.node === taskRef.node && (taskRef.vmid === null || t.guest_id === taskRef.vmid));
  }
  const taskConnections = taskRef ? connections.filter((c) => c === taskRef.connection) : connections;
  // Speicher-Meldung mit `?tab=storage&host=<knoten>`: die Pools dieses Knotens markieren.
  const storageFocus = tab === "storage" && focusHost ? (hosts ?? []).find((h) => h.id === focusHost) : undefined;
  const storageRef = storageFocus ? parseProviderRef(storageFocus.provider_ref) : null;

  const shieldHrefFor = (u: NodeUpdates) => {
    const node = nodes.find((h) => {
      const ref = parseProviderRef(h.provider_ref);
      return ref.connection === u.connection && ref.node === u.node;
    });
    return node ? shieldLinks[node.id] : undefined;
  };
  const nodeUpdates = (node: HostOut) => {
    const ref = parseProviderRef(node.provider_ref);
    return updates?.find((u) => u.connection === ref.connection && u.node === ref.node);
  };
  const ownUpdates = (updates ?? []).filter((u) => connections.includes(u.connection));
  const ownPools = (storage ?? []).filter((p) => connections.includes(p.connection));

  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <h2 className="font-semibold text-xl tracking-tight">Proxmox</h2>
      <p className="mb-4 text-sm opacity-60">Deine Proxmox-Server mit allen VMs und Containern: starten, herunterfahren, Snapshots, Speicher und Updates.</p>
      {empty && (
        <ConnectionsPanel onConnectWithPassword={canConnect ? () => setConnectOpen(true) : undefined} version={connectionsVersion} />
      )}
      {empty && connectOpen && (
        <ProxmoxConnect
          onClose={() => setConnectOpen(false)}
          onConnected={() => {
            setConnectionsVersion((n) => n + 1);
            setConnectedAt(Date.now());
            load();
          }}
        />
      )}
      {empty && !connectOpen && (
        <EmptyState
          icon="refresh"
          title="Noch keine Proxmox-Server gefunden"
          text={
            canConnect
              ? "Am einfachsten: „Mit Passwort verbinden“ – Adresse und Passwort deines Proxmox-Servers eingeben, Nodvard Deck legt dort einen eigenen Zugang an. Oder trag einen API-Token von Hand ein (oben unter „Verbindungen verwalten“ oder in den Moduleinstellungen). Danach liest Nodvard Deck alle 5 Minuten alle Knoten, VMs und Container von selbst ein."
              : "Trag dafür die Adresse deines Proxmox-Servers und einen API-Token ein (oben unter „Verbindungen verwalten“ oder in den Moduleinstellungen). Danach liest Nodvard Deck alle 5 Minuten alle Knoten, VMs und Container von selbst ein."
          }
          action={
            <div className="flex flex-wrap items-center justify-center gap-2">
              {canConnect && <Button variant="primary" onClick={() => setConnectOpen(true)}>Mit Passwort verbinden</Button>}
              <SettingsLink to="/settings/extensions/proxmox" permission="extensions.manage">Proxmox einrichten</SettingsLink>
            </div>
          }
        />
      )}

      {!empty && (
        <>
          <Summary
            nodes={nodes}
            guests={guests}
            connections={connections}
            updates={updatesLoadError ? "error" : updates ? ownUpdates : null}
            pools={storageLoadError ? "error" : storage ? ownPools : null}
            updatesIncomplete={updatesErrors.some((e) => connections.includes(e.connection))}
            storageIncomplete={storageErrors.some((e) => connections.includes(e.connection))}
            onOpen={setTab}
          />

          <TabBar
            active={tab}
            onSelect={setTab}
            badges={{
              updates: updatesCount(ownUpdates) > 0 ? { text: String(updatesCount(ownUpdates)), label: `${updatesCount(ownUpdates)} Pakete warten` } : undefined,
              tasks: tasksLoadError ? { text: "!", label: "nicht abrufbar" } : undefined,
            }}
          />

          {[...actionMessages].map(([key, text]) => (
            <p key={key} className="mb-3 text-sm opacity-80">{text}</p>
          ))}

          <TabPanel id="guests" active={tab}>
            {connections.map((connection) => (
              <section key={connection} className="mb-6">
                <h3 className="mb-2 text-sm font-medium opacity-60">{connection}</h3>

                {nodes
                  .filter((n) => parseProviderRef(n.provider_ref).connection === connection)
                  .map((node) => (
                    <NodeCard
                      key={node.id} node={node} focused={node.id === focusHost && tab === "guests"} visit={visits}
                      updates={nodeUpdates(node)} onOpenUpdates={() => setTab("updates")}
                    />
                  ))}

                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-left text-xs opacity-60">
                      <th className="py-1 w-6" />
                      <th className="py-1 sm:w-1/2">Name</th>
                      <th className="hidden py-1 whitespace-nowrap sm:table-cell sm:w-28">Status</th>
                      <th className="py-1 text-right">Aktionen</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {guests
                      .filter((g) => parseProviderRef(g.provider_ref).connection === connection)
                      .map((host) => {
                        const { node } = parseProviderRef(host.provider_ref);
                        const isExpanded = expanded === host.id;
                        const visible = VM_ACTIONS.filter((a) => !a.showWhen || a.showWhen(host.status));
                        const runAction = (action: (typeof VM_ACTIONS)[number]) =>
                          void trigger(host.id, host.display_name, action.type, action.confirm, {}, action.dangerConfirm);
                        const isBusy = (type: string) => pending.has(`${host.id}:${type}`);
                        return (
                          <Fragment key={host.id}>
                            <tr id={`host-${host.id}`} className={host.id === focusHost ? FOCUS_CLASS : undefined}>
                              <td className="py-1.5 align-top">
                                <button
                                  type="button"
                                  onClick={() => setExpanded(isExpanded ? null : host.id)}
                                  aria-label={isExpanded ? "Details einklappen" : "Details anzeigen"}
                                  className="opacity-60 hover:opacity-100"
                                >
                                  {isExpanded ? "▾" : "▸"}
                                </button>
                              </td>
                              <td className="py-1.5 break-words">
                                <span className="font-medium">{host.display_name}</span>
                                <span className="block text-xs opacity-60">
                                  {KIND_LABEL[host.kind ?? ""] ?? host.kind} · {node}
                                </span>
                                {/* Am Handy fehlt die Spalte „Status“: dort steht er unter dem Namen. */}
                                <span className={`block text-xs sm:hidden ${STATUS_TONE[host.status] ?? ""}`}>{HOST_STATUS_LABEL[host.status] ?? host.status}</span>
                              </td>
                              <td className={`hidden py-1.5 whitespace-nowrap sm:table-cell ${STATUS_TONE[host.status] ?? ""}`}>{HOST_STATUS_LABEL[host.status] ?? host.status}</td>
                              <td className="py-1.5">
                                <div className="flex flex-wrap justify-end gap-1.5">
                                  {/* Konsole: Bildschirm direkt in Nodvard Deck (Kern-Seite
                                      /console/:hostId), ohne Proxmox-Login. Neuer Tab, damit die
                                      Konsole neben dieser Liste offen bleiben kann. Eine gestoppte
                                      VM hat keinen Bildschirm -- Proxmox wuerde den Aufruf ablehnen. */}
                                  {host.status === "up" ? (
                                    <a
                                      href={`/console/${host.id}`}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="accent-soft rounded-lg px-2.5 py-1 text-xs hover:brightness-125"
                                    >
                                      Konsole
                                    </a>
                                  ) : (
                                    <span
                                      title="Nur für laufende VMs/Container verfügbar."
                                      className="cursor-not-allowed rounded-lg bg-white/5 px-2.5 py-1 text-xs opacity-40"
                                    >
                                      Konsole
                                    </span>
                                  )}
                                  {/* Starten und Herunterfahren direkt, der Rest (seltener, teils riskant) unter „Mehr“. */}
                                  {visible.filter((a) => a.main).map((action) => (
                                    <button
                                      key={action.type}
                                      type="button"
                                      disabled={isBusy(action.type)}
                                      onClick={() => runAction(action)}
                                      className="px-2.5 py-1 text-xs disabled:opacity-40 border rounded-lg transition border-white/10 bg-white/[0.06] hover:bg-white/[0.12]"
                                    >
                                      {isBusy(action.type) ? "…" : action.label}
                                    </button>
                                  ))}
                                  {visible.some((a) => !a.main) && (
                                    <MoreMenu
                                      small
                                      align="right"
                                      label={visible.some((a) => !a.main && isBusy(a.type)) ? "Mehr …" : "Mehr"}
                                      title={`Weitere Aktionen für ${host.display_name}`}
                                      testId={`more-${host.id}`}
                                      items={visible.filter((a) => !a.main).map((action) => ({
                                        id: action.type,
                                        label: isBusy(action.type) ? `${action.label} (läuft …)` : action.label,
                                        disabled: isBusy(action.type),
                                        danger: action.variant === "danger",
                                        run: () => runAction(action),
                                      }))}
                                    />
                                  )}
                                </div>
                              </td>
                            </tr>
                            {isExpanded && (
                              <tr>
                                <td />
                                <td colSpan={3} className="bg-black/20 py-2">
                                  <MetricsPanel hostId={host.id} />
                                  <GuestDetailsPanel hostId={host.id} />
                                  <p className="mt-2 mb-1 text-xs font-medium opacity-60">Snapshots</p>
                                  <SnapshotsPanel
                                    hostId={host.id}
                                    refreshKey={snapshotRefresh}
                                    isPending={(actionType, snapname) => pending.has(pendingKey(host.id, actionType, snapname))}
                                    onAction={(actionType, snapname) =>
                                      void trigger(
                                        host.id,
                                        host.display_name,
                                        actionType,
                                        actionType === "vm.snapshot_rollback"
                                          ? `auf Snapshot "${snapname}" zurückrollen? Der Gast wird dabei gestoppt, alles danach geht verloren.`
                                          : `Snapshot "${snapname}" löschen?`,
                                        { snapname },
                                      )
                                    }
                                  />
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                  </tbody>
                </table>
                <UnreachableHint connection={connection} sources={[["Updates", updatesErrors], ["Speicher", storageErrors]]} />
              </section>
            ))}
          </TabPanel>

          <TabPanel id="storage" active={tab}>
            {storageLoadError && <LoadError what="Die Liste der Speicher" error={storageLoadError} onRetry={loadStorage} />}
            {!storage && !storageLoadError && <p className="text-sm opacity-60">Lade Speicher …</p>}
            {connections.map((connection) => (
              <section key={connection} id={`storage-${connection}`} className="mb-6">
                <h3 className="mb-2 text-sm font-medium opacity-60">{connection}</h3>
                {storage && !storage.some((p) => p.connection === connection) && !storageErrors.some((e) => e.connection === connection) && (
                  <p className="text-xs opacity-60">Keine Speicher gefunden.</p>
                )}
                {storage && storage.some((p) => p.connection === connection) && (
                  <StorageSection
                    pools={storage.filter((p) => p.connection === connection)}
                    focusNode={storageRef?.connection === connection ? storageRef.node : null}
                  />
                )}
                <UnreachableHint connection={connection} sources={[["Speicher", storageErrors]]} testId={`unreachable-storage-${connection}`} />
              </section>
            ))}
          </TabPanel>

          <TabPanel id="updates" active={tab}>
            {updatesLoadError && <LoadError what="Die Liste der Updates" error={updatesLoadError} onRetry={loadUpdates} />}
            {!updates && !updatesLoadError && <p className="text-sm opacity-60">Lade Updates …</p>}
            {updates && (
              <p className="mb-3 text-xs opacity-60">
                Hier steht, was auf den Knoten wartet. Einspielen kannst du die Updates in Nodvard Shield unter „Updates“ (dafür braucht
                der Knoten einen SSH-Zugang) oder wie gewohnt in der Proxmox-Oberfläche.
              </p>
            )}
            {connections.map((connection) => (
              <section key={connection} className="mb-6">
                <h3 className="mb-2 text-sm font-medium opacity-60">{connection}</h3>
                {updates && updates.some((u) => u.connection === connection) && (
                  <UpdatesSection nodes={updates.filter((u) => u.connection === connection)} shieldHref={shieldHrefFor} />
                )}
                {updates && !updates.some((u) => u.connection === connection) && !updatesErrors.some((e) => e.connection === connection) && (
                  <p className="text-xs opacity-60">Keine Knoten gefunden.</p>
                )}
                <UnreachableHint connection={connection} sources={[["Updates", updatesErrors]]} testId={`unreachable-updates-${connection}`} />
              </section>
            ))}
          </TabPanel>

          <TabPanel id="tasks" active={tab}>
            {tasksLoadError && <LoadError what="Der Aufgabenverlauf" error={tasksLoadError} onRetry={loadTasks} />}
            {!tasks && !tasksLoadError && <p className="text-sm opacity-60">Lade Aufgabenverlauf …</p>}
            <div className="mb-3 flex flex-wrap items-center gap-3 text-xs opacity-80">
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={showConsoleTasks} onChange={() => setShowConsoleTasks((v) => !v)} />
                Konsolen-Öffnungen anzeigen
              </label>
              {taskHost && (
                <span className="accent-soft flex items-center gap-1 rounded px-2 py-0.5">
                  Nur {taskHost.display_name}
                  <button
                    type="button"
                    onClick={() => updateUrl({ tasks: null, tab: "tasks" })}
                    aria-label="Filter entfernen"
                    className="opacity-70 hover:opacity-100"
                  >
                    ✕
                  </button>
                </span>
              )}
            </div>
            {tasks && taskConnections.map((connection) => {
              const list = tasksFor(connection);
              return (
                <section key={connection} id={`tasks-${connection}`} className="mb-6">
                  <h3 className="mb-1 text-sm font-medium opacity-60">{connection}</h3>
                  {list.length > 0 && (
                    <p className="mb-1 text-xs opacity-60" data-testid={`tasks-count-${connection}`}>
                      {list.length === 1 ? "1 Aufgabe" : `${list.length} Aufgaben`}
                    </p>
                  )}
                  <TasksTable tasks={list} />
                </section>
              );
            })}
          </TabPanel>

          <TabPanel id="connections" active={tab}>
            {/* Hier statt oben auf der Seite: sonst öffnete der Knopf das Formular außer Sicht (Handy, gescrollt). */}
            {connectOpen && (
              <ProxmoxConnect
                onClose={() => setConnectOpen(false)}
                onConnected={() => {
                  setConnectionsVersion((n) => n + 1);
                  setConnectedAt(Date.now());
                  load();
                }}
              />
            )}
            <ConnectionsPanel bare onConnectWithPassword={canConnect ? () => setConnectOpen(true) : undefined} version={connectionsVersion} />
          </TabPanel>
        </>
      )}
    </div>
  );
}

type TabId = "guests" | "storage" | "updates" | "tasks" | "connections";

const TABS: { id: TabId; label: string }[] = [
  { id: "guests", label: "VMs & Container" },
  { id: "storage", label: "Speicher" },
  { id: "updates", label: "Updates" },
  { id: "tasks", label: "Aufgabenverlauf" },
  { id: "connections", label: "Verbindungen" },
];

function isTab(value: string | null): value is TabId {
  return TABS.some((t) => t.id === value);
}

/**
 * Reiterleiste nach dem üblichen Muster: nur der offene Reiter ist ein Tab-Stopp, die Pfeiltasten (sowie Pos1/Ende)
 * wechseln. Der offene Reiter wird in den sichtbaren Bereich geholt (am Handy passen nicht alle nebeneinander).
 */
function TabBar({ active, onSelect, badges }: {
  active: TabId;
  onSelect: (id: TabId) => void;
  badges: Partial<Record<TabId, { text: string; label: string }>>;
}): JSX.Element {
  const refs = useRef<Partial<Record<TabId, HTMLButtonElement | null>>>({});
  const bar = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    // Nur die Leiste waagerecht schieben, nicht die Seite: die scrollt ein Link gleich zu seinem Ziel.
    const tab = refs.current[active];
    if (!tab || !bar.current) return;
    const t = tab.getBoundingClientRect();
    const b = bar.current.getBoundingClientRect();
    if (t.left < b.left || t.right > b.right) bar.current.scrollLeft += t.left - b.left - 16;
  }, [active]);
  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    const index = TABS.findIndex((t) => t.id === active);
    const next = e.key === "ArrowRight" ? (index + 1) % TABS.length
      : e.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length
        : e.key === "Home" ? 0
          : e.key === "End" ? TABS.length - 1
            : -1;
    if (next < 0) return;
    e.preventDefault();
    onSelect(TABS[next].id);
    refs.current[TABS[next].id]?.focus();
  }
  return (
    <div ref={bar} role="tablist" aria-label="Bereiche" className="mb-4 flex gap-1 overflow-x-auto border-b border-white/[0.08] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {TABS.map((t) => {
        const badge = badges[t.id];
        return (
          <button
            key={t.id}
            ref={(el) => { refs.current[t.id] = el; }}
            type="button"
            role="tab"
            id={`pve-tab-${t.id}`}
            aria-selected={active === t.id}
            aria-controls={`pve-panel-${t.id}`}
            tabIndex={active === t.id ? 0 : -1}
            onClick={() => onSelect(t.id)}
            onKeyDown={onKeyDown}
            className={`-mb-px whitespace-nowrap border-b-2 px-3.5 py-2 text-sm transition ${
              active === t.id ? "border-[var(--color-accent)] text-white" : "border-transparent text-white/55 hover:text-white"
            }`}
          >
            {t.label}
            {badge && (
              <span className="ml-1.5 rounded-full bg-amber-500/80 px-1.5 text-[10px] text-black" title={badge.label}>
                <span aria-hidden="true">{badge.text}</span>
                <span className="sr-only">{`: ${badge.label}`}</span>
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** Reiterinhalt: bleibt eingehängt, wenn ein anderer Reiter offen ist (aufgeklappte Zeilen und Eingaben bleiben). */
function TabPanel({ id, active, children }: { id: TabId; active: TabId; children: ReactNode }): JSX.Element {
  return (
    <div role="tabpanel" id={`pve-panel-${id}`} aria-labelledby={`pve-tab-${id}`} hidden={active !== id} tabIndex={0} className="outline-none">
      {children}
    </div>
  );
}

/** Pakete, die auf allen Knoten zusammen warten. */
function updatesCount(nodes: NodeUpdates[]): number {
  return nodes.reduce((sum, n) => sum + (n.count ?? 0), 0);
}

const STATUS_TONE: Record<string, string> = { up: "text-emerald-300", down: "opacity-60", maintenance: "text-amber-300" };

const TILE_TONE: Record<string, string> = { good: "text-emerald-300", warn: "text-amber-300", bad: "text-red-300", neutral: "text-white" };

function Tile({ label, value, hint, tone = "neutral", onClick, testId }: {
  label: string; value: string; hint: string; tone?: "good" | "warn" | "bad" | "neutral"; onClick: () => void; testId: string;
}): JSX.Element {
  return (
    <button type="button" onClick={onClick} data-testid={testId} className="panel px-4 py-3 text-left transition hover:bg-white/[0.04]">
      <span className="block text-xs text-white/50">{label}</span>
      <span className={`mt-1 block text-xl font-semibold tabular-nums ${TILE_TONE[tone]}`}>{value}</span>
      <span className="mt-0.5 block text-[11px] text-white/40">{hint}</span>
    </button>
  );
}

/**
 * Das Wichtigste auf einen Blick: laufen die Knoten und Gäste, warten Updates, wird ein Speicher voll?
 * Ein Klick auf eine Kachel öffnet den passenden Reiter.
 */
function Summary({ nodes, guests, connections, updates, pools, updatesIncomplete, storageIncomplete, onOpen }: {
  nodes: HostOut[];
  /** Verbindungen mit Hosts auf dieser Seite: „aktuell“ nur, wenn jede davon Knoten mit Updates geliefert hat. */
  connections: string[];
  guests: HostOut[];
  /** null: lädt noch; "error": Abfrage gescheitert. */
  updates: NodeUpdates[] | null | "error";
  pools: StoragePool[] | null | "error";
  /** Eine Verbindung oder ein Knoten war nicht erreichbar: dann nie „aktuell“ behaupten. */
  updatesIncomplete: boolean;
  storageIncomplete: boolean;
  onOpen: (tab: TabId) => void;
}): JSX.Element {
  const nodesUp = nodes.filter((n) => n.status === "up").length;
  const guestsUp = guests.filter((g) => g.status === "up").length;
  const guestsDown = guests.filter((g) => g.status === "down").length;

  let updatesTile: { value: string; hint: string; tone: "good" | "warn" | "bad" | "neutral" };
  if (updates === "error") updatesTile = { value: "?", hint: "gerade nicht abrufbar", tone: "warn" };
  else if (updates === null) updatesTile = { value: "…", hint: "wird geladen", tone: "neutral" };
  else {
    const count = updatesCount(updates);
    const reboot = updates.some((u) => u.reboot_pending);
    const incomplete = updatesIncomplete || updates.some((u) => u.error)
      || connections.some((c) => !updates.some((u) => u.connection === c));
    // Die Liste stammt aus dem Prüflauf von Proxmox: ist der gescheitert oder lange her, ist „aktuell“ nicht sicher.
    const outdated = updates.some((u) => !u.error && (u.last_check_ok === false || u.last_check_stale));
    if (count > 0) updatesTile = { value: String(count), hint: count === 1 ? "Paket wartet" : "Pakete warten", tone: "warn" };
    else if (reboot) updatesTile = { value: "Neustart", hint: "Neustart nötig, damit ein Update gilt", tone: "warn" };
    else if (incomplete) updatesTile = { value: "?", hint: "nicht alle Knoten abrufbar", tone: "warn" };
    else if (outdated) updatesTile = { value: "?", hint: "Prüfung auf Updates veraltet oder fehlgeschlagen", tone: "warn" };
    else updatesTile = { value: "aktuell", hint: "alles auf dem neuesten Stand", tone: "good" };
    if (count > 0 && reboot) updatesTile.hint += " · Neustart nötig";
    if ((count > 0 || reboot) && incomplete) updatesTile.hint += " · nicht alles abrufbar";
  }

  let storageTile: { value: string; hint: string; tone: "good" | "warn" | "bad" | "neutral" };
  if (pools === "error") storageTile = { value: "?", hint: "gerade nicht abrufbar", tone: "warn" };
  else if (pools === null) storageTile = { value: "…", hint: "wird geladen", tone: "neutral" };
  else {
    const fullest = [...pools].filter((p) => p.used_percent != null).sort((a, b) => (b.used_percent ?? 0) - (a.used_percent ?? 0))[0];
    // Ein Speicher, den Proxmox als nicht aktiv meldet, hat keine Belegung -- er wiegt schwerer als jeder volle.
    const unavailable = pools.find((p) => p.used_percent == null && p.tone === "danger");
    storageTile = unavailable
      ? { value: "!", hint: `${unavailable.storage}${unavailable.shared ? "" : ` (${unavailable.node})`} nicht verfügbar`, tone: "bad" }
      : fullest
      ? {
          value: `${Math.round(fullest.used_percent ?? 0)} %`,
          hint: `am vollsten: ${fullest.storage}${fullest.shared ? "" : ` (${fullest.node})`}`,
          tone: fullest.tone === "danger" ? "bad" : fullest.tone === "warn" ? "warn" : "good",
        }
      : storageIncomplete
        ? { value: "?", hint: "gerade nicht abrufbar", tone: "warn" }
        : { value: "–", hint: "keine Speicher gefunden", tone: "neutral" };
    if (fullest && !unavailable && storageIncomplete) {
      storageTile.hint += " · nicht alles abrufbar";
      if (storageTile.tone === "good") storageTile.tone = "warn";
    }
  }

  return (
    <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="pve-summary">
      <Tile
        testId="tile-nodes" label="Knoten" value={`${nodesUp}/${nodes.length}`} hint="online"
        tone={nodes.length === 0 ? "neutral" : nodesUp < nodes.length ? "bad" : "good"} onClick={() => onOpen("guests")}
      />
      <Tile
        testId="tile-guests" label="VMs & Container" value={`${guestsUp}/${guests.length}`}
        hint={guestsDown > 0 ? `laufen · ${guestsDown} gestoppt` : "laufen"} onClick={() => onOpen("guests")}
      />
      <Tile testId="tile-updates" label="Updates" {...updatesTile} onClick={() => onOpen("updates")} />
      <Tile testId="tile-storage" label="Speicher" {...storageTile} onClick={() => onOpen("storage")} />
    </div>
  );
}
