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
 * Aufbau: eine Detailseite statt einer flachen Tabelle -- nach Verbindung (mehrere
 * Proxmox-Instanzen) UND Knoten gruppiert (`provider_ref =
 * "<connection>/<kind>/<node>/<vmid>"`, siehe capabilities.py), jede VM/jeder
 * Container klappt auf und zeigt echte Live-Metriken (`GET /hosts/{id}/metrics`,
 * CPU/RAM/Laufzeit) statt nur Name+Status+Buttons.
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
import type { FormEvent } from "react";

import { ACTION_STATUS_LABEL, type ActionState, isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody, errorText } from "../../../_shared/frontend/src/api";
import { formatBytes as formatSize } from "../../../_shared/frontend/src/format";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { EmptyState } from "../../../_shared/frontend/src/ui";
import { FOCUS_CLASS, deck } from "../../../_shared/frontend/src/deck";

interface ConnectionOut {
  name: string;
  base_url: string;
  token_id: string;
  tls_insecure_skip_verify: boolean;
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
}[] = [
  { type: "vm.start", label: "Starten", showWhen: (s) => s !== "up" },
  {
    type: "vm.shutdown",
    label: "Herunterfahren",
    confirm: "sauber herunterfahren? Das Betriebssystem in der VM fährt geordnet herunter.",
    dangerConfirm: false,
    showWhen: (s) => s !== "down",
  },
  { type: "vm.reboot", label: "Neustarten", confirm: "wirklich neu starten?", showWhen: (s) => s !== "down" },
  { type: "vm.snapshot", label: "Snapshot" },
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
  "vm.snapshot": "Snapshot anlegen",
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
  packages: { package: string; title: string | null; old_version: string | null; version: string; new_package: boolean }[];
}

const BADGE_TONE: Record<string, string> = {
  good: "bg-emerald-500/20 text-emerald-300",
  warn: "bg-amber-500/20 text-amber-300",
  danger: "bg-red-500/20 text-red-300",
  neutral: "bg-white/10 opacity-70",
};

/**
 * Paket-Updates je Knoten -- rein lesend. Installieren bleibt bewusst beim Nutzer
 * (Proxmox-Shell oder Terminal von Nodvard Deck); hier steht nur, WAS wartet und ob danach ein
 * Neustart faellig ist.
 */
function UpdatesSection({ nodes }: { nodes: NodeUpdates[] }): JSX.Element {
  return (
    <div className="mt-3">
      <h4 className="mb-1 text-xs font-medium uppercase tracking-wide opacity-60">Updates</h4>
      <ul className="space-y-2">
        {nodes.map((n) => (
          <li key={n.node} className="text-sm" data-testid={`updates-${n.node}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{n.node}</span>
              <span className={`rounded px-1.5 py-0.5 text-xs ${BADGE_TONE[n.tone] ?? BADGE_TONE.neutral}`}>{n.badge}</span>
              <span className="opacity-80">{n.summary}</span>
            </div>
            {!n.error && (
              <p className="text-xs opacity-60">
                Proxmox {n.pve_version ?? "?"} · Kernel {n.running_kernel ?? "?"}
                {n.last_check ? ` · zuletzt geprüft ${new Date(n.last_check * 1000).toLocaleString()}` : ""}
                {n.last_check_ok === false ? " (Prüfung fehlgeschlagen)" : ""}
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
        ))}
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
function UnreachableHint({ connection, sources }: { connection: string; sources: [string, FetchError[]][] }): JSX.Element | null {
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
    <div className="mt-3 space-y-1 text-sm text-amber-300" data-testid={`unreachable-${connection}`}>
      {[...lines].map(([rest, whats]) => (
        <p key={rest}>{`${whats.join(" und ")} ${rest}`}</p>
      ))}
    </div>
  );
}

function StorageSection({ pools }: { pools: StoragePool[] }): JSX.Element {
  return (
    <div className="mt-3">
      <h4 className="mb-1 text-xs font-medium uppercase tracking-wide opacity-60">Speicher</h4>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/10 text-left text-xs uppercase opacity-60">
            <th className="py-1 w-1/5">Pool</th>
            <th className="py-1">Inhalt</th>
            <th className="py-1 w-1/3">Belegung</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {pools.map((pool) => (
            <tr key={pool.id} data-testid={`pool-${pool.storage}`}>
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
 * Aufgabenverlauf: was auf den Knoten passiert ist, wer es ausgeloest hat, ob es
 * geklappt hat -- z. B. geplante Neustarts oder Backups, die sonst nur in der
 * Proxmox-Oberflaeche sichtbar waeren.
 */
function TasksSection({
  id,
  tasks,
  showConsole,
  onToggleConsole,
  focused = false,
  visit = 0,
  filterLabel,
  onClearFilter,
}: {
  id?: string;
  tasks: TaskOut[];
  showConsole: boolean;
  onToggleConsole: () => void;
  /** Ein Link (`?tasks=1`) fuehrt hierher: aufklappen -- bei jedem Link (`visit`) erneut. */
  focused?: boolean;
  visit?: number;
  filterLabel?: string;
  onClearFilter?: () => void;
}): JSX.Element {
  // Nur aufklappen, nie zuklappen: nach dem Entfernen des Filters bleibt die volle Liste offen.
  const ref = useRef<HTMLDetailsElement | null>(null);
  useEffect(() => {
    if (focused && ref.current) ref.current.open = true;
  }, [focused, visit]);
  return (
    <details ref={ref} id={id} className="mt-3 p-2 panel">
      <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide opacity-70">Aufgabenverlauf ({tasks.length})</summary>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs opacity-80">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={showConsole} onChange={onToggleConsole} />
          Konsolen-Öffnungen anzeigen
        </label>
        {filterLabel && (
          <span className="accent-soft flex items-center gap-1 rounded px-2 py-0.5">
            Nur {filterLabel}
            <button type="button" onClick={onClearFilter} aria-label="Filter entfernen" className="opacity-70 hover:opacity-100">
              ✕
            </button>
          </span>
        )}
      </div>
      <table className="mt-1 w-full text-xs">
        <thead>
          <tr className="border-b border-white/10 text-left uppercase opacity-60">
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
    </details>
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
    const ok = await deck().confirmDialog(`Hardware ändern -- ${summary}? Manches greift erst nach einem Neustart des Gasts.`, { confirmLabel: "Ändern" });
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
        onDone(`Änderung vorgeschlagen -- Freigabe durch einen Admin nötig, siehe "Aktionen".`);
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
    </div>
  );
}

/** Knoten-Karte: zugeklappt nur Name + Status; aufgeklappt Hardware + Datentraeger
 * (erst dann geladen -- SMART-Abfragen kosten auf pve2 spuerbar Zeit). */
function NodeCard({ node, focused = false, visit = 0 }: { node: HostOut; focused?: boolean; visit?: number }): JSX.Element {
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
      <span className="opacity-60">(Knoten) -- {HOST_STATUS_LABEL[node.status] ?? node.status}</span>
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
function ConnectionsPanel(): JSX.Element {
  const [connections, setConnections] = useState<ConnectionOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBaseUrl, setNewBaseUrl] = useState("");
  const [newTokenId, setNewTokenId] = useState("");
  const [newTlsInsecure, setNewTlsInsecure] = useState(false);
  const [pending, setPending] = useState<string | null>(null);

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
  }, [load]);

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
      setMessage(`Verbindung "${newName}" angelegt -- jetzt noch ein Token setzen.`);
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
      const res = await authedFetch(`/extensions/proxmox/secrets`, {
        method: "PUT", body: JSON.stringify({ label: `proxmox-token:${name}`, value }),
      });
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
      `Verbindung "${name}" wirklich entfernen? Ein bereits gesetztes Token bleibt im Tresor stehen.`,
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

  return (
    <details className="mb-6 p-3 panel" open={connections?.length === 0}>
      <summary className="cursor-pointer text-sm font-medium">Verbindungen verwalten</summary>
      <div className="mt-3">
        {message && <p className="mb-2 text-xs opacity-80">{message}</p>}
        {connections && connections.length === 0 && (
          <p className="mb-2 text-xs opacity-60">Noch keine Verbindung konfiguriert.</p>
        )}
        {connections && connections.length > 0 && (
          <table className="mb-3 w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 text-left uppercase opacity-60">
                <th className="py-1 w-1/6">Name</th>
                <th className="py-1 w-1/3">Adresse</th>
                <th className="py-1 whitespace-nowrap">Zertifikat nicht prüfen</th>
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
                  <td className="py-1.5 whitespace-nowrap opacity-70">{c.tls_insecure_skip_verify ? "ja" : "nein"}</td>
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

        {!showAddForm && (
          <button
            type="button"
            onClick={() => setShowAddForm(true)}
            className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
          >
            + Neue Verbindung
          </button>
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
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={newTlsInsecure} onChange={(e) => setNewTlsInsecure(e.target.checked)} />
              Zertifikat nicht prüfen
            </label>
            <button type="submit" disabled={pending === "add"} className="px-2 py-1 disabled:opacity-40 accent-gradient text-white rounded-lg shadow-md shadow-black/30 hover:brightness-110 font-medium">
              Anlegen
            </button>
            <button type="button" onClick={() => setShowAddForm(false)} className="px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
              Abbrechen
            </button>
          </form>
        )}
      </div>
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

  useEffect(() => {
    authedFetch("/ext/proxmox/updates")
      .then((res) => (res.ok ? (res.json() as Promise<{ nodes: NodeUpdates[]; errors?: FetchError[] }>) : { nodes: [] }))
      .then((body) => {
        setUpdates(body.nodes);
        setUpdatesErrors(body.errors ?? []);
      })
      .catch(() => setUpdates([]));
  }, []);
  const [showConsoleTasks, setShowConsoleTasks] = useState(false);

  useEffect(() => {
    authedFetch(`/ext/proxmox/tasks?limit=100${showConsoleTasks ? "&include_console=true" : ""}`)
      .then((res) => (res.ok ? (res.json() as Promise<{ tasks: TaskOut[] }>) : { tasks: [] }))
      .then((body) => setTasks(body.tasks))
      .catch(() => setTasks([]));
  }, [showConsoleTasks, snapshotRefresh]);

  useEffect(() => {
    authedFetch("/ext/proxmox/storage")
      .then((res) => (res.ok ? (res.json() as Promise<{ pools: StoragePool[]; errors?: FetchError[] }>) : { pools: [] }))
      .then((body) => {
        setStorage(body.pools);
        setStorageErrors(body.errors ?? []);
      })
      .catch(() => setStorage([]));
  }, []);

  const load = useCallback(() => {
    setError(null);
    authedFetch("/hosts?tag=proxmox")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<HostOut[]>;
      })
      .then(setHosts)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Einmal je Link hinscrollen, sobald die Zeile existiert -- nicht bei jedem Neuladen.
  const scrolledFor = useRef<number | null>(null);
  useEffect(() => {
    if (!hosts || !focusHost || scrolledFor.current === visits) return;
    const target = hosts.find((h) => h.id === focusHost);
    if (!target) return;
    if (focusTasks && !tasks) return;
    scrolledFor.current = visits;
    scrollToElement(focusTasks ? `tasks-${parseProviderRef(target.provider_ref).connection}` : `host-${target.id}`);
  }, [hosts, tasks, focusHost, focusTasks, visits]);

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
        setMessage(key, `${hostLabel}: ${actionLabel(actionType, snapname)} vorgeschlagen -- Freigabe durch einen Admin nötig, siehe "Aktionen".`);
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
  if (error) return <div className="p-6 text-sm text-red-400">Fehler: {error}</div>;

  // Der Abgleich legt Knoten als kind="hypervisor" an -- ein Filter nur auf "node"
  // zeigte deshalb nie einen Knoten an.
  const nodes = (hosts ?? []).filter((h) => h.kind === "hypervisor" || h.kind === "node");
  const guests = (hosts ?? []).filter((h) => h.kind === "vm" || h.kind === "lxc");
  const connections = [...new Set((hosts ?? []).map((h) => parseProviderRef(h.provider_ref).connection))].sort();

  // Aufgabenverlauf nur fuer den Host, von dessen Server-Seite der Nutzer kommt.
  const taskHost = (hosts ?? []).find((h) => h.id === taskFocus) ?? null;
  const taskRef = taskHost ? parseProviderRef(taskHost.provider_ref) : null;
  function tasksFor(connection: string): TaskOut[] {
    const all = (tasks ?? []).filter((t) => t.connection === connection);
    if (!taskRef || taskRef.connection !== connection) return all;
    return all.filter((t) => t.node === taskRef.node && (taskRef.vmid === null || t.guest_id === taskRef.vmid));
  }

  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <h2 className="mb-4 font-semibold text-xl tracking-tight">Proxmox-Knoten &amp; VMs</h2>
      <ConnectionsPanel />
      {[...actionMessages].map(([key, text]) => (
        <p key={key} className="mb-3 text-sm opacity-80">{text}</p>
      ))}
      {hosts && hosts.length === 0 && (
        <EmptyState
          icon="refresh"
          title="Noch keine Proxmox-Server gefunden"
          text="Trag dafür die Adresse deines Proxmox-Servers und einen API-Token ein (oben unter „Verbindungen verwalten“ oder in den Moduleinstellungen). Danach liest Nodvard Deck alle 5 Minuten alle Knoten, VMs und Container von selbst ein."
          action={<SettingsLink to="/settings/extensions/proxmox" permission="extensions.manage">Proxmox einrichten</SettingsLink>}
        />
      )}

      {connections.map((connection) => (
        <section key={connection} className="mb-6">
          <h3 className="mb-2 text-sm font-medium uppercase tracking-wide opacity-60">{connection}</h3>

          {nodes
            .filter((n) => parseProviderRef(n.provider_ref).connection === connection)
            .map((node) => (
              <NodeCard key={node.id} node={node} focused={node.id === focusHost} visit={visits} />
            ))}

          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-xs uppercase opacity-60">
                <th className="py-1 w-6" />
                <th className="py-1 w-1/3">Name</th>
                <th className="py-1 whitespace-nowrap">Typ</th>
                <th className="py-1 whitespace-nowrap">Knoten</th>
                <th className="py-1 whitespace-nowrap">Status</th>
                <th className="py-1">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {guests
                .filter((g) => parseProviderRef(g.provider_ref).connection === connection)
                .map((host) => {
                  const { node } = parseProviderRef(host.provider_ref);
                  const isExpanded = expanded === host.id;
                  return (
                    <Fragment key={host.id}>
                      <tr id={`host-${host.id}`} className={host.id === focusHost ? FOCUS_CLASS : undefined}>
                        <td className="py-1.5">
                          <button
                            type="button"
                            onClick={() => setExpanded(isExpanded ? null : host.id)}
                            aria-label={isExpanded ? "Details einklappen" : "Details anzeigen"}
                            className="opacity-60 hover:opacity-100"
                          >
                            {isExpanded ? "▾" : "▸"}
                          </button>
                        </td>
                        <td className="py-1.5 break-words">{host.display_name}</td>
                        <td className="py-1.5 whitespace-nowrap opacity-70">{KIND_LABEL[host.kind ?? ""] ?? host.kind}</td>
                        <td className="py-1.5 whitespace-nowrap opacity-70">{node}</td>
                        <td className="py-1.5 whitespace-nowrap">{HOST_STATUS_LABEL[host.status] ?? host.status}</td>
                        <td className="py-1.5">
                          <div className="flex flex-wrap gap-1.5">
                            {/* Konsole: Bildschirm direkt in Nodvard Deck (Kern-Seite
                                /console/:hostId), ohne Proxmox-Login. Neuer Tab, damit die
                                Konsole neben dieser Liste offen bleiben kann. Eine gestoppte
                                VM hat keinen Bildschirm -- Proxmox wuerde den Aufruf ablehnen. */}
                            {host.status === "up" ? (
                              <a
                                href={`/console/${host.id}`}
                                target="_blank"
                                rel="noreferrer"
                                className="accent-soft rounded px-2 py-1 text-xs hover:brightness-125"
                              >
                                Konsole
                              </a>
                            ) : (
                              <span
                                title="Nur für laufende VMs/Container verfügbar."
                                className="cursor-not-allowed rounded bg-white/5 px-2 py-1 text-xs opacity-40"
                              >
                                Konsole
                              </span>
                            )}
                            {VM_ACTIONS.filter((a) => !a.showWhen || a.showWhen(host.status)).map((action) => (
                              <button
                                key={action.type}
                                type="button"
                                disabled={pending.has(`${host.id}:${action.type}`)}
                                onClick={() => void trigger(host.id, host.display_name, action.type, action.confirm, {}, action.dangerConfirm)}
                                className={`px-2 py-1 text-xs disabled:opacity-40 border rounded-lg transition ${
                                  action.variant === "danger"
                                    ? "border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25"
                                    : "border-white/10 bg-white/[0.06] hover:bg-white/[0.12]"
                                }`}
                              >
                                {pending.has(`${host.id}:${action.type}`) ? "…" : action.label}
                              </button>
                            ))}
                          </div>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td />
                          <td colSpan={5} className="bg-black/20 py-2">
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
          {updates && updates.some((u) => u.connection === connection) && (
            <UpdatesSection nodes={updates.filter((u) => u.connection === connection)} />
          )}
          {storage && storage.some((p) => p.connection === connection) && (
            <StorageSection pools={storage.filter((p) => p.connection === connection)} />
          )}
          {tasks && (
            <TasksSection
              id={`tasks-${connection}`}
              tasks={tasksFor(connection)}
              showConsole={showConsoleTasks}
              onToggleConsole={() => setShowConsoleTasks((v) => !v)}
              focused={taskRef?.connection === connection}
              visit={visits}
              filterLabel={taskRef?.connection === connection ? taskHost?.display_name : undefined}
              onClearFilter={() => updateUrl({ tasks: null })}
            />
          )}
        </section>
      ))}
    </div>
  );
}
