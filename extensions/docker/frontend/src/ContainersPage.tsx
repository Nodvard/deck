/**
 * Docker-Seite: Start, Stopp, Neustart und Live-Logs je Container, damit man dafür weder Portainer noch SSH braucht.
 *
 * Aktionen laufen über `POST /hosts/{id}/actions/container.*` wie die Proxmox-Knöpfe: Der Nutzer steht als Auslöser im
 * Aktions-Journal, Sperrliste, Anti-Flapping und Selbstständigkeit greifen. Wer `actions.approve:<risk>` hat, gibt den
 * eigenen Vorschlag direkt frei (der Bestätigungsdialog davor ist die Bestätigung), sonst wartet er auf einen Admin.
 */
import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";

import { ACTION_STATUS_LABEL, runAction } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorText } from "../../../_shared/frontend/src/api";
import { formatBytes } from "../../../_shared/frontend/src/format";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { SettingsLink, safeHref, useSettingText } from "../../../_shared/frontend/src/links";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { Button, EmptyState } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

import { BulkProposeButton } from "./BulkPropose";
import { runHostAction } from "./hostAction";
import { ImageUpdateAction, UpdatePanel, type AppliedState, type ApplyingState, type ImageApply } from "./ImageUpdateApply";
import { LogPanel } from "./Logs";
import { OPERATE_NO_IMAGE_CHECK, OperateBadge } from "./operate";
import { RegistriesSection } from "./Registries";
import { RESOURCE_TABS, ResourcesSection, type ResourceKind } from "./Resources";
import { StacksSection } from "./Stacks";
import { ToStackPanel } from "./ToStack";

interface ServiceEntry {
  id: string;
  name: string;
  host: string;
  host_id?: string;
  container?: string;
  is_self?: boolean;
  image?: string;
  compose_project?: string | null;
  state: string;
  status: string;
  tone: string;
  url: string | null;
  /** Von Nodvard Deck erkannte Adresse; `url` ist die eigene, wenn `url_custom`. */
  url_detected?: string | null;
  url_custom?: boolean;
  /** Server im Modus „Nur bedienen“: nur Ansehen, Logs, Starten, Stoppen und Neustarten. */
  operate_only?: boolean;
}

type Verb = "start" | "stop" | "restart" | "remove";

interface ContainerStats {
  cpu_percent: number | null;
  mem_used: number | null;
  mem_limit: number | null;
}

/** Was Docker auf einem Host an CPU und Arbeitsspeicher sieht; im LXC-Container sind das dessen Grenzen. */
export interface HostEnvironment {
  virtualization: "lxc" | "container" | "vm" | "none" | null;
  cpus: number;
  mem_total: number | null;
  docker_version: string | null;
}

const MEASURING_HINT = "CPU/RAM werden gemessen – das braucht je Server ein paar Sekunden.";
/** RAM „unbekannt“ statt „0 B“: Auf dem Raspberry Pi zählt der Kernel standardmäßig keinen Speicher je Container. */
const MEM_UNKNOWN_HINT = "Der Server meldet keinen Speicherverbrauch je Container (auf dem Raspberry Pi ist die cgroup-Speicherabrechnung standardmäßig aus).";

const TONE_CLASS: Record<string, string> = {
  good: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  warn: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  danger: "bg-red-500/15 text-red-300 border-red-500/40",
  neutral: "bg-white/10 opacity-70",
};

/** Eine Zeile der Container-Tabelle wird am Handy zur Karte (siehe Tabelle unten). */
const CARD_ROW =
  "max-md:flex max-md:flex-wrap max-md:items-center max-md:gap-x-3 max-md:gap-y-2 max-md:rounded-lg max-md:border max-md:border-white/10 max-md:bg-white/[0.03] max-md:p-3";
/** Wert mit eigener Beschriftung (`data-label`), solange die Spaltenköpfe fehlen. */
const CARD_LABEL = "max-md:before:mr-1 max-md:before:opacity-60 max-md:before:content-[attr(data-label)]";
/** Größere Knöpfe am Handy: ein Finger trifft die kleinen Schaltflächen sonst kaum. */
const TOUCH = "max-md:px-3 max-md:py-2";

/** Dockers Zustandswörter auf Deutsch; die Farbe kommt vom `tone` des Backends, nicht vom Text. */
export const STATE_LABEL: Record<string, string> = {
  running: "läuft",
  exited: "gestoppt",
  created: "erstellt",
  restarting: "startet neu",
  paused: "pausiert",
  removing: "wird entfernt",
  dead: "tot",
  error: "Fehler",
};

/** Zustands-Filter (`?state=`). „Mit Problemen“ zählt auch laufende, die Docker als ungesund meldet, abgestürzte und solche,
 * die Docker in letzter Zeit mehrmals neu starten musste. */
export const STATE_FILTERS: { value: string; label: string }[] = [
  { value: "running", label: "Läuft" },
  { value: "stopped", label: "Gestoppt oder pausiert" },
  { value: "problems", label: "Mit Problemen" },
];

/** Ab so vielen Neustarts durch Docker (`RestartCount`) gilt ein Container als auffällig, wenn der letzte Start keine
 * Stunde her ist; danach läuft er offenbar wieder stabil, der Zähler bleibt aber stehen. */
export const MANY_RESTARTS = 3;
const RECENT_START = /^(Up (Less than a second|\d+ seconds?|About a minute|\d+ minutes?)|Restarting)\b/;

export function restartsAreAProblem(restarts: number | undefined, status: string): boolean {
  return (restarts ?? 0) >= MANY_RESTARTS && RECENT_START.test(status);
}

/** Exit-Codes nach einem normalen `docker stop` (SIGTERM, nach der Frist SIGKILL) oder Strg+C; alles andere ist ein Absturz. */
const STOP_CODES = new Set([0, 130, 137, 143]);

function crashed(status: string): boolean {
  const code = /^Exited \((\d+)\)/.exec(status)?.[1];
  return code !== undefined && !STOP_CODES.has(Number(code));
}

export function inStateFilter(entry: { state: string; status: string }, filter: string, restarts = 0): boolean {
  if (filter === "running") return entry.state === "running";
  if (filter === "stopped") return ["exited", "created", "paused"].includes(entry.state);
  if (filter === "problems") {
    return ["restarting", "dead", "removing", "error"].includes(entry.state) || /\(unhealthy\)/.test(entry.status)
      || crashed(entry.status) || restartsAreAProblem(restarts, entry.status);
  }
  return true;
}

/** `?stack=-`: nur Container ohne Compose-Projekt; ein Projektname beginnt nie mit „-“. */
const NO_STACK = "-";

/** Der Platzhalter eines nicht erreichbaren Servers trägt seine Server-ID nur in `id` (`<id>:__error__`). */
function hostIdOf(entry: { id: string; host_id?: string }): string | undefined {
  return entry.host_id ?? (entry.id.endsWith(":__error__") ? entry.id.slice(0, -":__error__".length) : undefined);
}

export function environmentText(env: HostEnvironment): { text: string; title: string } {
  const limits = [`${env.cpus} CPU`, ...(env.mem_total ? [`${formatBytes(env.mem_total)} RAM`] : [])].join(" · ");
  if (env.virtualization === "lxc") {
    return {
      text: `im LXC-Container, Grenze ${limits}`,
      title: "Docker läuft hier in einem LXC-Container und sieht nur dessen Anteil. CPU und RAM der Container zählen innerhalb dieser Grenze, nicht für den ganzen Rechner darunter.",
    };
  }
  if (env.virtualization === "vm") return { text: `VM mit ${limits}`, title: "Docker läuft hier in einer virtuellen Maschine; das sind ihre Werte." };
  if (env.virtualization === "container") return { text: `im Container, ${limits}`, title: "Docker läuft hier selbst in einem Container; das sind die Werte, die er sieht." };
  return { text: limits, title: "So viel CPU und RAM sieht Docker auf diesem Server." };
}

function FilterSelect({ label, all, value, options, onChange, testId }: {
  label: string; all: string; value: string | null; options: [string, string][]; onChange: (value: string | null) => void; testId?: string;
}): JSX.Element {
  return (
    <select aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} data-testid={testId}
      className="max-w-[12rem] px-2 py-1 text-sm rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]">
      <option value="">{all}</option>
      {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
    </select>
  );
}

/** Zeiteinheiten: Einzahl, Mehrzahl (jeweils im Dativ, wie nach „seit“ und „vor“) und der Artikel für „etwa …“. */
const DURATION_UNITS: Record<string, { one: string; many: string; about: string }> = {
  second: { one: "Sekunde", many: "Sekunden", about: "einer" },
  minute: { one: "Minute", many: "Minuten", about: "einer" },
  hour: { one: "Stunde", many: "Stunden", about: "einer" },
  day: { one: "Tag", many: "Tagen", about: "einem" },
  week: { one: "Woche", many: "Wochen", about: "einer" },
  month: { one: "Monat", many: "Monaten", about: "einem" },
  year: { one: "Jahr", many: "Jahren", about: "einem" },
};

/** Dockers Zeitangabe („3 hours“, „About an hour“, „Less than a second“) in der Wortform nach „seit“ und „vor“. */
function durationText(raw: string): string | null {
  const text = raw.trim().toLowerCase();
  if (text === "less than a second") return "weniger als einer Sekunde";
  const about = /^about an? (second|minute|hour|day|week|month|year)$/.exec(text);
  if (about) return `etwa ${DURATION_UNITS[about[1]].about} ${DURATION_UNITS[about[1]].one}`;
  const counted = /^(\d+) (second|minute|hour|day|week|month|year)s?$/.exec(text);
  if (!counted) return null;
  const n = Number(counted[1]);
  const unit = DURATION_UNITS[counted[2]];
  return `${n} ${n === 1 ? unit.one : unit.many}`;
}

/** Dockers Status-Zeile („Up 3 hours“, „Exited (0) 10 hours ago“) auf Deutsch; was nicht passt, bleibt wie es ist. */
export function statusText(status: string | null | undefined): string {
  const raw = (status ?? "").trim();
  if (!raw) return "";
  const health = (h: string | undefined) => (!h ? "" : h === "healthy" ? " (gesund)" : h === "unhealthy" ? " (nicht gesund)" : h === "health: starting" ? " (wird geprüft)" : ` (${h})`);
  const up = /^Up (.+?)(?: \(((?:un)?healthy|health: starting)\))?(?: \(Paused\))?$/i.exec(raw);
  if (up) {
    const d = durationText(up[1]);
    if (d) return `Läuft seit ${d}${health(up[2]?.toLowerCase())}${/\(Paused\)$/i.test(raw) ? " (pausiert)" : ""}`;
  }
  const ended = /^Exited \((-?\d+)\) (.+) ago$/i.exec(raw);
  if (ended) {
    const d = durationText(ended[2]);
    if (d) return `Beendet (Code ${ended[1]}) vor ${d}`;
  }
  const restarting = /^Restarting \((-?\d+)\) (.+) ago$/i.exec(raw);
  if (restarting) {
    const d = durationText(restarting[2]);
    if (d) return `Startet neu (Code ${restarting[1]}), zuletzt vor ${d}`;
  }
  const plain: Record<string, string> = { created: "Angelegt, noch nicht gestartet", paused: "Pausiert", dead: "Defekt", "removal in progress": "Wird entfernt" };
  return plain[raw.toLowerCase()] ?? raw;
}

/** Ergebnis der Image-Prüfung je laufendem Container; der Schlüssel ist wie `ServiceEntry.id` (`host_id:container`).
 * `local`: selbst gebautes Image, ein klares Ergebnis und kein Problem (es gibt keine Registry). */
export interface ImageResult {
  image: string;
  status: "current" | "update" | "local" | "unknown";
  reason: string | null;
  remote_digest: string | null;
  registry_at: string | null;
  /** Die Registry hat diesmal nicht geantwortet: `remote_digest` ist ihre letzte Antwort. */
  stale?: boolean;
  /** Nur bei „update“: lässt sich der Container hier einspielen (`compose`) oder warum nicht (`none`). */
  apply?: ImageApply;
}
interface ImageHostState {
  checking: boolean;
  checked_at: string | null;
  error: string | null;
}
interface ImageUpdates {
  data: Record<string, ImageResult>;
  hosts: Record<string, ImageHostState>;
  /** Laufende Updates (Schlüssel wie `data`) und das Ergebnis der zuletzt beendeten. */
  applying?: Record<string, ApplyingState>;
  applied?: Record<string, AppliedState>;
}

const IMAGE_POLL_MS = 2000;

/** Nur der Stand des Hosts `hostId` (Server-Filter `?host=`), damit Zusammenfassung, „Prüfe Images …“ und Knopf zur Tabelle passen. */
export function scopeImages(images: ImageUpdates | null, hostId: string | null): ImageUpdates | null {
  if (!images || !hostId) return images;
  const prefix = `${hostId}:`;
  const only = <T,>(record: Record<string, T> | undefined) =>
    Object.fromEntries(Object.entries(record ?? {}).filter(([key]) => key.startsWith(prefix)));
  return {
    ...images,
    data: only(images.data),
    hosts: Object.fromEntries(Object.entries(images.hosts ?? {}).filter(([id]) => id === hostId)),
    applying: only(images.applying),
    applied: only(images.applied),
  };
}

/** Zeile über der Tabelle. „alles aktuell“ steht nur, wenn etwas geprüft wurde und nichts offen blieb (Abruflimit,
 * unerreichbare Registry oder stummer Host sprechen dagegen). Selbst gebaute Container sind kein Problem, zählen aber
 * nicht als geprüft. `operateServers`: Server im Modus „Nur bedienen“, die nie geprüft werden. */
export function summarizeImages(images: ImageUpdates | null, checking: boolean, operateServers = 0): string | null {
  if (checking) return "Image-Updates: Die Image-Quellen werden gefragt …";
  if (!images) return null;
  const results = Object.values(images.data ?? {});
  const hosts = Object.values(images.hosts ?? {});
  const failedHosts = hosts.filter((h) => h.error).length;
  const lastCheck = hosts.map((h) => h.checked_at).filter((t): t is string => Boolean(t)).sort().pop();
  const applying = Object.keys(images.applying ?? {}).length;
  const applyingText = applying === 1 ? "1 Update wird eingespielt" : `${applying} Updates werden eingespielt`;
  if (!lastCheck && failedHosts === 0) return applying > 0 ? `Image-Updates: ${applyingText}` : null;
  const head = lastCheck ? `Image-Updates geprüft ${new Date(lastCheck).toLocaleString()}` : "Image-Updates";
  const notChecked = operateServers > 0 ? ` · ${operateServers} Server im Modus „Nur bedienen“ nicht geprüft` : "";

  const count = (status: ImageResult["status"]) => results.filter((r) => r.status === status).length;
  const update = count("update");
  const current = count("current");
  const local = count("local");
  const unknown = count("unknown");
  const stale = results.filter((r) => r.stale).length;
  if (results.length === 0) {
    return `${head} · ${failedHosts > 0 ? "Prüfung nicht möglich" : "keine laufenden Container"}${applying > 0 ? ` · ${applyingText}` : ""}${notChecked}`;
  }

  const parts: string[] = [];
  const open = update > 0 || unknown > 0 || stale > 0 || failedHosts > 0;
  if (!open && current > 0) {
    parts.push("alles aktuell");
  } else {
    if (update > 0) parts.push(`${update} mit Update`);
    if (current > 0) parts.push(`${current} aktuell`);
  }
  if (local > 0) parts.push(`${local} selbst gebaut`);
  if (unknown > 0) parts.push(`${unknown} nicht prüfbar`);
  if (stale > 0) parts.push(`${stale} mit älterer Antwort der Registry`);
  if (failedHosts > 0) parts.push(failedHosts === 1 ? "1 Server nicht erreichbar" : `${failedHosts} Server nicht erreichbar`);
  if (applying > 0) parts.push(applyingText);
  return `${head} · ${parts.join(" · ")}${notChecked}`;
}

/** Hinweis, wenn die Registry diesmal nicht geantwortet hat und die letzte Antwort gilt. */
function StaleNote({ result }: { result: ImageResult }): JSX.Element | null {
  if (!result.stale) return null;
  const at = result.registry_at ? ` Letzte Antwort der Registry: ${new Date(result.registry_at).toLocaleString()}.` : "";
  return <span className="mt-0.5 block text-[11px] opacity-60" title={`${result.reason ?? ""}${at}`}>alter Stand</span>;
}

/** Graues Badge mit dem Grund als kleinem Hinweis darunter ("selbst gebaut", "nicht prüfbar"). */
function NeutralBadge({ label, reason }: { label: string; reason: string | null }): JSX.Element {
  return (
    <span title={reason ?? undefined}>
      <span className={`rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.neutral}`}>{label}</span>
      {reason && <span className="mt-0.5 block max-w-[15rem] whitespace-normal break-words text-[11px] opacity-60">{reason}</span>}
    </span>
  );
}

/** Badge in der Spalte „Image-Update“. `result` fehlt, wenn der Container noch nicht (gestoppte gar nicht) geprüft wurde.
 * `operateOnly`: Server im Modus „Nur bedienen“, dort wird nie geprüft. */
export function ImageUpdateBadge({ result, checking = false, running = true, operateOnly = false }: {
  result?: ImageResult;
  checking?: boolean;
  running?: boolean;
  operateOnly?: boolean;
}): JSX.Element {
  if (operateOnly) return <span className="text-xs opacity-40" title={OPERATE_NO_IMAGE_CHECK}>–</span>;
  if (!result) {
    return checking
      ? <span className="text-xs opacity-60">prüft …</span>
      : <span className="text-xs opacity-40" title={running ? "Noch nicht geprüft." : "Gestoppte Container werden nicht geprüft."}>–</span>;
  }
  if (result.status === "current") {
    return (
      <span>
        <span className={`rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.good}`} title={result.image}>aktuell</span>
        <StaleNote result={result} />
      </span>
    );
  }
  if (result.status === "update") {
    const short = result.remote_digest?.replace("sha256:", "").slice(0, 12);
    return (
      <span>
        <span
          className={`rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.warn}`}
          title={`${result.image}: Bei der Registry liegt eine neuere Version${short ? ` (${short})` : ""}.`}
        >
          Update verfügbar
        </span>
        <StaleNote result={result} />
      </span>
    );
  }
  return <NeutralBadge label={result.status === "local" ? "selbst gebaut" : "nicht prüfbar"} reason={result.reason} />;
}

/** Ergebnis der Prüfung nach der Aktion (`after_check.py`): läuft der Container danach stabil? Nur bei Start/Neustart. */
export function afterCheckText(action: { result?: { detail?: Record<string, unknown> | null } | null }): string | null {
  const check = action.result?.detail?.check as { text?: unknown } | undefined;
  return typeof check?.text === "string" && check.text ? check.text : null;
}

const VERB_TEXT: Record<Verb, { label: string; done: string; confirm?: string }> = {
  start: { label: "Starten", done: "gestartet" },
  stop: { label: "Stoppen", done: "gestoppt", confirm: "wirklich stoppen? Der Dienst ist danach nicht erreichbar." },
  restart: { label: "Neustart", done: "neu gestartet", confirm: "wirklich neu starten?" },
  remove: {
    label: "Löschen", done: "gelöscht",
    confirm: "wirklich löschen? Volumes und das Image bleiben, aber was nur im Container selbst lag, ist weg.",
  },
};

/** Shell im Container: die Terminal-Seite des Kerns mit `scope=container` (ScopedTerminalTarget in shell.py). */
export function consoleHref(hostId: string, container: string): string {
  return `/terminal?${new URLSearchParams({ host: hostId, scope: "container", ref: container }).toString()}`;
}

/** Nur gestoppte Container lassen sich löschen (wie `_REMOVABLE_STATES` im Backend). */
const REMOVABLE_STATES = new Set(["exited", "created", "dead"]);

interface DiskRow { type: string; label: string; total: number | null; active: number | null; size: number | null; reclaimable: number | null }
interface ImageRow { id: string; name: string | null; dangling: boolean; size: number | null; created: string | null; in_use: boolean | null }

const PRUNE_ACTIONS: { type: string; label: string; confirm: string }[] = [
  { type: "docker.prune_images", label: "Verwaiste Images entfernen", confirm: "Images ohne Namen entfernen, die kein Container nutzt?" },
  { type: "docker.prune_unused_images", label: "Ungenutzte Images entfernen", confirm: "Alle Images ohne Container entfernen – auch benannte? Werden sie wieder gebraucht, lädt Docker sie neu herunter." },
  { type: "docker.prune_build_cache", label: "Build-Cache leeren", confirm: "Build-Cache leeren? Der nächste docker build auf diesem Server dauert dann deutlich länger." },
];

/** Docker-Speicher eines Hosts: wofür der Platz draufgeht und welche Images ungenutzt sind; Aufräumen läuft über die Freigabe. */
function StoragePanel({ hostId }: { hostId: string }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState<{ disk: DiskRow[]; images: ImageRow[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    authedFetch(`/ext/docker/hosts/${hostId}/docker`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<{ disk: DiskRow[]; images: ImageRow[] }>;
      })
      .then((body) => { if (!cancelled) setData(body); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [hostId, reload]);

  async function prune(action: (typeof PRUNE_ACTIONS)[number]) {
    const ok = await deck().confirmDialog(action.confirm, { confirmLabel: action.label });
    if (!ok) return;
    setBusy(action.type);
    setMessage(null);
    try {
      setMessage(`${action.label}: ${await runHostAction(hostId, action.type, `${action.label} über die Docker-Seite.`, unmountSignal())}`);
      setReload((n) => n + 1);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <p className="text-xs text-red-400">Docker-Speicher nicht abrufbar: {error}</p>;
  if (!data) return <p className="text-xs opacity-60">Lade Docker-Speicher … (docker system df braucht ein paar Sekunden)</p>;
  const canRun = deck().hasPermission("hosts.execute");
  return (
    <div className="text-xs" data-testid={`storage-${hostId}`}>
      <div className="mb-2 flex flex-wrap gap-2">
        {data.disk.map((d) => (
          <div key={d.type} className="px-2.5 py-1.5 panel">
            <span className="block opacity-60">{d.label}{d.total != null ? ` (${d.active ?? "?"}/${d.total} aktiv)` : ""}</span>
            <span className="text-sm font-medium">{d.size != null ? formatBytes(d.size) : "?"}</span>
            {d.reclaimable ? <span className="ml-1.5 text-amber-300">{formatBytes(d.reclaimable)} frei machbar</span> : null}
          </div>
        ))}
      </div>
      {canRun && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {PRUNE_ACTIONS.map((a) => (
            <button key={a.type} type="button" disabled={busy !== null} onClick={() => void prune(a)}
              className="px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
              {busy === a.type ? "…" : a.label}
            </button>
          ))}
        </div>
      )}
      {message && <p className="mb-2 opacity-90" role="status">{message}</p>}
      {data.images.length > 0 && (
        <table className="w-full">
          <thead>
            <tr className="border-b border-white/10 text-left opacity-60">
              <th className="py-1">Image</th><th className="py-1">Größe</th><th className="py-1">Erstellt</th><th className="py-1">Genutzt</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {data.images.map((img) => (
              <tr key={img.id}>
                <td className="py-1 break-words">{img.name ?? <span className="opacity-60">ohne Namen ({img.id})</span>}</td>
                <td className="py-1 whitespace-nowrap">{img.size != null ? formatBytes(img.size) : "?"}</td>
                <td className="py-1 opacity-70">{img.created ?? ""}</td>
                <td className={`py-1 ${img.in_use === false ? "text-amber-300" : "opacity-70"}`}>
                  {img.in_use == null ? "?" : img.in_use ? "ja" : "nein"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface InspectOut {
  image: string | null;
  image_id: string | null;
  created: string | null;
  started_at: string | null;
  finished_at: string | null;
  running: boolean;
  exit_code: number | null;
  oom_killed: boolean;
  health: string | null;
  restart_policy: string;
  restart_count: number;
  privileged: boolean;
  network_mode: string | null;
  memory_limit: number | null;
  ports: { container: string; published: string[] }[];
  mounts: { type: string | null; source: string | null; destination: string | null; read_only: boolean }[];
  networks: { name: string; ip: string | null }[];
  env_keys: string[];
  compose: { project: string; service: string | null; working_dir: string | null } | null;
}

const RESTART_LABEL: Record<string, string> = {
  no: "nie", always: "immer", "unless-stopped": "außer manuell gestoppt", "on-failure": "bei Fehler",
};
const HEALTH_LABEL: Record<string, string> = { healthy: "gesund", unhealthy: "ungesund", starting: "startet" };

/** Container-Details; von den Umgebungsvariablen nur die Namen, die Werte sind oft Passwörter. */
function DetailsPanel({ entry, onChanged }: { entry: ServiceEntry; onChanged?: (message: string, warn?: boolean) => void }): JSX.Element {
  const [data, setData] = useState<InspectOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toStack, setToStack] = useState(false);
  useEffect(() => {
    let cancelled = false;
    authedFetch(`/ext/docker/containers/${entry.host_id}/${encodeURIComponent(entry.container!)}/inspect`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<InspectOut>;
      })
      .then((body) => { if (!cancelled) setData(body); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [entry.host_id, entry.container]);

  if (error) return <p className="text-xs text-red-400">Details nicht abrufbar: {error}</p>;
  if (!data) return <p className="text-xs opacity-60">Lade Details …</p>;
  const d = data;
  const since = d.running ? d.started_at : d.finished_at;
  return (
    <div className="text-xs" data-testid={`inspect-${entry.id}`}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 opacity-85 sm:grid-cols-4">
        <div><dt className="opacity-60">Image</dt><dd className="break-words">{d.image ?? "?"}{d.image_id ? ` (${d.image_id})` : ""}</dd></div>
        <div><dt className="opacity-60">{d.running ? "Läuft seit" : "Beendet"}</dt><dd>{since ? new Date(since).toLocaleString() : "–"}{!d.running && d.exit_code != null ? ` · Exit ${d.exit_code}` : ""}{d.oom_killed ? " · Speicher voll (OOM)" : ""}</dd></div>
        <div><dt className="opacity-60">Neustart-Regel</dt><dd>{RESTART_LABEL[d.restart_policy] ?? d.restart_policy}{d.restart_count ? ` · ${d.restart_count}× neu gestartet` : ""}</dd></div>
        <div><dt className="opacity-60">Zustand</dt><dd>{d.health ? HEALTH_LABEL[d.health] ?? d.health : "kein Healthcheck"}{d.privileged ? " · privilegiert" : ""}</dd></div>
        {d.compose && <div><dt className="opacity-60">Compose</dt><dd className="break-words">{d.compose.project}{d.compose.service ? ` / ${d.compose.service}` : ""}{d.compose.working_dir ? ` · ${d.compose.working_dir}` : ""}</dd></div>}
        {d.memory_limit ? <div><dt className="opacity-60">RAM-Limit</dt><dd>{formatBytes(d.memory_limit)}</dd></div> : null}
      </dl>
      <p className="mt-2 mb-0.5 font-medium opacity-60">Ports</p>
      <p className="opacity-85">{d.ports.length === 0 ? (d.network_mode === "host" ? "Host-Netz (alle Ports direkt)" : "keine") : d.ports.map((p) => `${p.published.length ? p.published.join(", ") : "intern"} → ${p.container}`).join(" · ")}</p>
      <p className="mt-2 mb-0.5 font-medium opacity-60">Speicher</p>
      <ul className="space-y-0.5 opacity-85">
        {d.mounts.length === 0 && <li>keine Mounts</li>}
        {d.mounts.map((m) => (
          <li key={`${m.destination}`} className="break-words">
            <span className="opacity-60">{m.type === "volume" ? "Volume" : m.type === "bind" ? "Ordner" : m.type}</span> {m.source} → {m.destination}{m.read_only ? " (nur lesen)" : ""}
          </li>
        ))}
      </ul>
      <p className="mt-2 mb-0.5 font-medium opacity-60">Netze</p>
      <p className="opacity-85">{d.networks.map((n) => `${n.name}${n.ip ? ` (${n.ip})` : ""}`).join(" · ") || "keine"}</p>
      <p className="mt-2 mb-0.5 font-medium opacity-60">Umgebungsvariablen ({d.env_keys.length})</p>
      <p className="break-words opacity-70" title="Werte werden bewusst nicht angezeigt – dort stehen oft Passwörter.">{d.env_keys.join(", ") || "keine"}</p>
      {!d.compose && !entry.operate_only && entry.host_id && entry.container && onChanged && deck().hasPermission("hosts.execute") && (
        <div className="mt-2">
          <Button small pressed={toStack} onClick={() => setToStack((open) => !open)}>In Stack umwandeln</Button>
          {toStack && <ToStackPanel hostId={entry.host_id} container={entry.container} onDone={(text, warn) => { setToStack(false); onChanged(text, warn); }} />}
        </div>
      )}
    </div>
  );
}

/** Eigene Adresse für die Kachel (`PUT .../url`), z. B. wenn die erkannte auf den falschen Port zeigt; sie bleibt auch nach
 * dem Neuerstellen des Containers. Vorbelegt ist nur eine eigene Adresse: Mit der erkannten als Wert würde „Speichern“
 * ohne Änderung sie als eigene festschreiben, und spätere Verbesserungen der Erkennung kämen nicht mehr an. */
function UrlPanel({ entry, onSaved, onClose }: { entry: ServiceEntry; onSaved: () => void; onClose: () => void }): JSX.Element {
  const current = entry.url_custom ? entry.url ?? "" : "";
  const [value, setValue] = useState(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (url: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const res = await authedFetch(`/ext/docker/containers/${encodeURIComponent(entry.host_id!)}/${encodeURIComponent(entry.container!)}/url`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) throw new Error(await errorText(res));
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="space-y-2 text-xs" onSubmit={(e) => { e.preventDefault(); if (value.trim() !== current) void save(value.trim() || null); }}>
      <label className="block">
        <span className="mb-1 block opacity-70">Adresse, die „Öffnen“ und die Kachel aufrufen</span>
        <input type="url" value={value} onChange={(e) => setValue(e.target.value)} placeholder={entry.url_detected ?? "http://192.0.2.10:8080"}
          className="w-full rounded border border-white/10 bg-black/30 px-2 py-1.5 sm:max-w-md" />
      </label>
      <p className="opacity-60">
        {entry.url_detected ? `Erkannt: ${entry.url_detected}` : "Nodvard Deck hat für diesen Container keine Weboberfläche erkannt."}
      </p>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <div className="flex flex-wrap gap-1.5">
        <button type="submit" disabled={busy || value.trim() === current} className={`rounded bg-emerald-500/20 px-2 py-1 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40 ${TOUCH}`}>
          {busy ? "…" : "Speichern"}
        </button>
        {entry.url_custom && (
          <button type="button" disabled={busy} onClick={() => void save(null)} className={`rounded bg-white/10 px-2 py-1 hover:bg-white/20 disabled:opacity-40 ${TOUCH}`}>
            Erkannte nehmen
          </button>
        )}
        <button type="button" onClick={onClose} className={`rounded px-2 py-1 opacity-70 hover:opacity-100 ${TOUCH}`}>Abbrechen</button>
      </div>
    </form>
  );
}

/** „Öffnen“ nur für http- und https-Adressen: Eine eigene Adresse wie `javascript:…` wird nie ein Link. */
function OpenLink({ url }: { url: string | null | undefined }) {
  const href = safeHref(url);
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="px-1 py-1 text-xs opacity-70 hover:opacity-100 hover:underline max-md:px-2 max-md:py-2">
      Öffnen
    </a>
  );
}

/** Leerzustand: sagt, welche Markierung ein Server braucht (die eingestellte, sonst „docker“). */
function NoContainers() {
  const tag = useSettingText("docker", "docker_host_tag", "docker");
  return (
    <EmptyState
      icon="package"
      title="Noch kein Server mit Docker"
      text={`Lege unter Server & Zugänge einen Server an und gib ihm die Markierung „${tag}“. Läuft dort Docker, erscheinen seine Container hier von selbst. Die Markierung lässt sich in den Einstellungen des Moduls ändern.`}
      action={
        <div className="flex flex-wrap justify-center gap-2">
          <SettingsLink to="/settings/hosts" permission="hosts.write">Server &amp; Zugänge öffnen</SettingsLink>
          <SettingsLink to="/settings/extensions/docker" permission="extensions.manage" variant="secondary">Moduleinstellungen</SettingsLink>
        </div>
      }
    />
  );
}

type Tab = ResourceKind | "stacks" | "registries";
const TABS: { kind: Tab; label: string }[] = [{ kind: "stacks", label: "Stacks" }, ...RESOURCE_TABS, { kind: "registries", label: "Registries" }];

/** Reiter Container, Stacks, Images, Volumes, Netze, Registries (`?tab=`); die Container-Liste fragt nur, solange ihr Reiter offen ist. */
export function ContainersPage(): JSX.Element {
  const [urlParams, updateUrl] = useUrlParams();
  // Die Listen öffnen SSH wie `/hosts/{id}/docker`, deshalb erst mit `hosts.execute`.
  const resourcesAllowed = deck().hasPermission("hosts.execute");
  const tab: Tab | null = (resourcesAllowed && TABS.find((t) => t.kind === urlParams.get("tab"))?.kind) || null;
  const tabs = resourcesAllowed && (
    <nav className="mb-4 flex flex-wrap gap-1.5" aria-label="Bereiche der Docker-Seite">
      {[{ kind: null, label: "Container" }, ...TABS].map((t) => (
        <Button key={t.label} small variant={tab === t.kind ? "primary" : "secondary"} pressed={tab === t.kind}
          onClick={() => updateUrl({ tab: t.kind })}>
          {t.label}
        </Button>
      ))}
    </nav>
  );
  if (!tab) return <ContainerList tabs={tabs} />;
  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <h2 className="mb-4 font-semibold text-xl tracking-tight">Docker</h2>
      {tabs}
      {tab === "stacks" ? (
        <StacksSection hostId={urlParams.get("host") || null} onHost={(id) => updateUrl({ host: id })} empty={<NoContainers />} />
      ) : tab === "registries" ? (
        <RegistriesSection hostId={urlParams.get("host") || null} onHost={(id) => updateUrl({ host: id })} empty={<NoContainers />} />
      ) : (
        <ResourcesSection kind={tab} hostId={urlParams.get("host") || null} onHost={(id) => updateUrl({ host: id })} empty={<NoContainers />} />
      )}
    </div>
  );
}

function ContainerList({ tabs }: { tabs: ReactNode }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [services, setServices] = useState<ServiceEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  // Offene Logs (`?logs=<id>`) und Server-Filter (`?host=`, Sprung von der Server-Seite des Kerns) stehen in der
  // Adresszeile und folgen ihr (location.ts), auch bei einem Link auf die schon offene Seite.
  const [urlParams, updateUrl] = useUrlParams();
  const logsFor = urlParams.get("logs") || null;
  const setLogsFor = (id: string | null) => updateUrl({ logs: id });
  const hostFilter = urlParams.get("host") || null;
  const stackFilter = urlParams.get("stack") || null;
  const stateFilter = urlParams.get("state") || null;
  const [detailsFor, setDetailsFor] = useState<string | null>(null);
  const [urlFor, setUrlFor] = useState<string | null>(null);
  const [updateFor, setUpdateFor] = useState<string | null>(null);
  const [storageFor, setStorageFor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [stats, setStats] = useState<Record<string, ContainerStats>>({});
  const [environments, setEnvironments] = useState<Record<string, HostEnvironment>>({});
  const [restarts, setRestarts] = useState<Record<string, number>>({});
  // Die Messung dauert ein paar Sekunden: „noch am Messen“ muss anders aussehen als „kein Wert“ („–“).
  const [statsLoaded, setStatsLoaded] = useState(false);
  // Zuletzt gespeicherter Stand der Image-Updates; während einer Prüfung im Hintergrund wird nachgefragt, bis alle Hosts fertig sind.
  const [images, setImages] = useState<ImageUpdates | null>(null);
  const [imageMessage, setImageMessage] = useState<string | null>(null);

  const loadImages = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await authedFetch("/ext/docker/image-updates", { signal });
      if (!res.ok) return;
      const body = (await res.json()) as ImageUpdates;
      if (!signal?.aborted) setImages(body);
    } catch {
      // Ohne Stand bleibt die Spalte bei „–“, die Seite funktioniert weiter.
    }
  }, []);

  const reloadImages = useCallback(() => void loadImages(unmountSignal()), [loadImages, unmountSignal]);

  const load = useCallback(() => {
    // Der Ladefehler verschwindet erst mit dem nächsten Erfolg, sonst blinkt er alle 30 s.
    // Die Image-Updates mitladen: Ergebnisse des Tagesjobs oder eines anderen Tabs erscheinen sonst erst nach dem Neuladen.
    void loadImages(unmountSignal());
    fetchJson<{ data: ServiceEntry[] }>("/ext/docker/widgets/matrix")
      .then((body) => { setServices(body.data); setError(null); })
      .catch((err: unknown) => setError(errorMessage(err)));
  }, [loadImages, unmountSignal]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  // Mit Server-Filter (`?host=`) zählt nur dieser Host, wie bei Tabelle und Prüfknöpfen.
  const visibleImages = scopeImages(images, hostFilter);
  const imagesChecking = Object.values(visibleImages?.hosts ?? {}).some((h) => h.checking);
  // Auch während ein Update läuft (`applying`): sein Schritt und das Ende sollen erscheinen.
  const imagesBusy = imagesChecking || Object.keys(visibleImages?.applying ?? {}).length > 0;
  useEffect(() => {
    if (!imagesBusy) return;
    const controller = new AbortController();
    const interval = setInterval(() => void loadImages(controller.signal), IMAGE_POLL_MS);
    return () => {
      clearInterval(interval);
      controller.abort();
    };
  }, [imagesBusy, loadImages]);

  // CPU/RAM getrennt von der Liste: `docker stats` braucht je Host ein paar Sekunden,
  // die Liste soll darauf nicht warten.
  useEffect(() => {
    let cancelled = false;
    const loadStats = () =>
      authedFetch("/ext/docker/stats")
        .then((res) => (res.ok
          ? (res.json() as Promise<{ data: Record<string, ContainerStats>; hosts?: Record<string, HostEnvironment>; restarts?: Record<string, number> }>)
          : { data: {}, hosts: undefined, restarts: undefined }))
        .then((body) => {
          if (cancelled) return;
          setStats(body.data);
          if (body.hosts) setEnvironments(body.hosts);
          if (body.restarts) setRestarts(body.restarts);
          setStatsLoaded(true);
        })
        .catch(() => !cancelled && setStatsLoaded(true));
    void loadStats();
    const interval = setInterval(loadStats, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  async function trigger(entry: ServiceEntry, verb: Verb) {
    const text = VERB_TEXT[verb];
    if (text.confirm) {
      const extra = entry.is_self && verb === "restart"
        ? " Das ist Nodvard Deck selbst – die Oberfläche ist kurz weg."
        : verb === "remove" && entry.compose_project
          ? ` Er gehört zum Compose-Projekt „${entry.compose_project}“: Beim nächsten „docker compose up“ (auch beim Einspielen eines Updates) legt Docker ihn wieder an.`
          : "";
      const ok = await deck().confirmDialog(`"${entry.name}" ${text.confirm}${extra}`, {
        danger: verb === "stop" || verb === "remove",
        confirmLabel: text.label,
      });
      if (!ok) return;
    }
    setPending(`${entry.id}:${verb}`);
    setMessage(null);
    try {
      const { action, approved } = await runAction(`/hosts/${entry.host_id}/actions/container.${verb}`, {
        method: "POST",
        body: JSON.stringify({ payload: { container: entry.container }, reason: `Über die Docker-Seite ausgelöst (${verb}).` }),
      }, { signal: unmountSignal() });
      if (!approved && action.status === "proposed") {
        setMessage(`"${entry.name}": ${text.label} vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
        return;
      }
      const status = action.status ?? "?";
      const failure = action.result?.error;
      const check = afterCheckText(action);
      setMessage(
        status === "succeeded"
          ? `"${entry.name}" ${text.done}.${check ? ` Prüfung danach: ${check}` : ""}`
          : `"${entry.name}": ${text.label} -> ${ACTION_STATUS_LABEL[status] ?? status}${failure ? ` (${failure})` : ""}.`,
      );
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function startImageCheck(force: boolean) {
    setImageMessage(null);
    const params = new URLSearchParams();
    if (hostFilter) params.set("host_id", hostFilter);
    if (force) params.set("force", "true");
    const query = params.toString();
    try {
      const res = await authedFetch(`/ext/docker/image-updates/check${query ? `?${query}` : ""}`, { method: "POST", signal: unmountSignal() });
      if (!res.ok) throw new Error(await errorText(res));
      setImages((await res.json()) as ImageUpdates);
    } catch (err) {
      setImageMessage(`Image-Prüfung: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function restartStack(entry: ServiceEntry) {
    const project = entry.compose_project!;
    const members = (services ?? []).filter((s) => s.host_id === entry.host_id && s.compose_project === project);
    const self = members.some((s) => s.is_self);
    const ok = await deck().confirmDialog(
      `Stack "${project}" neu starten (${members.map((s) => s.name).join(", ")})? Die Dienste sind kurz nicht erreichbar.${self ? " Darin läuft Nodvard Deck selbst – die Oberfläche ist kurz weg." : ""}`,
      { confirmLabel: "Neu starten" },
    );
    if (!ok) return;
    setPending(`stack:${entry.host_id}:${project}`);
    setMessage(null);
    try {
      const { action, approved } = await runAction(`/hosts/${entry.host_id}/actions/docker.stack_restart`, {
        method: "POST",
        body: JSON.stringify({ payload: { project }, reason: `Stack "${project}" über die Docker-Seite neu gestartet.` }),
      }, { signal: unmountSignal() });
      const status = action.status ?? "?";
      if (approved) {
        setMessage(
          status === "succeeded"
            ? action.result?.output ?? "Stack neu gestartet."
            : `Stack "${project}": ${ACTION_STATUS_LABEL[status] ?? status}${action.result?.error ? ` (${action.result.error})` : ""}`,
        );
      } else if (status === "proposed") {
        setMessage(`Stack "${project}": vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
      } else {
        setMessage(`Stack "${project}": ${ACTION_STATUS_LABEL[status] ?? status}`);
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  if (!services && !error) return <div className="p-4 sm:p-6">{tabs}<p className="text-sm opacity-60">Lade …</p></div>;
  // Scheitert erst das Nachladen (alle 30 s), bleibt die Tabelle mit dem letzten Stand stehen; der Hinweis steht darüber.
  if (error && !services) return <div className="p-4 sm:p-6">{tabs}<LoadError what="Die Liste der Container" error={error} onRetry={load} /></div>;

  // Server im Modus „Nur bedienen“ prüft Nodvard Deck nie auf neue Images.
  const scoped = (services ?? []).filter((s) => !hostFilter || hostIdOf(s) === hostFilter);
  const operateServers = new Set(scoped.filter((s) => s.operate_only).map((s) => hostIdOf(s))).size;
  const imageSummary = summarizeImages(visibleImages, imagesChecking, operateServers);

  const needle = query.trim().toLowerCase();
  const byHost = new Map<string, ServiceEntry[]>();
  for (const s of services ?? []) {
    if (needle && !s.name.toLowerCase().includes(needle) && !(s.compose_project ?? "").toLowerCase().includes(needle)) continue;
    if (hostFilter && hostIdOf(s) !== hostFilter) continue;
    // Ein nicht erreichbarer Server bleibt bei jedem Stack- und Zustands-Filter sichtbar, sonst sähe er aus wie ohne Treffer.
    if (s.state !== "error") {
      if (stackFilter && (stackFilter === NO_STACK ? Boolean(s.compose_project) : s.compose_project !== stackFilter)) continue;
      if (stateFilter && !inStateFilter(s, stateFilter, restarts[s.id])) continue;
    }
    const list = byHost.get(s.host) ?? [];
    list.push(s);
    byHost.set(s.host, list);
  }
  const hostOptions = new Map<string, string>();
  for (const s of services ?? []) {
    const id = hostIdOf(s);
    if (id && !hostOptions.has(id)) hostOptions.set(id, s.host);
  }
  if (hostFilter && !hostOptions.has(hostFilter)) hostOptions.set(hostFilter, "dieser Server");
  const stacks = new Set((services ?? []).filter((s) => !hostFilter || hostIdOf(s) === hostFilter).flatMap((s) => (s.compose_project ? [s.compose_project] : [])));
  if (stackFilter && stackFilter !== NO_STACK) stacks.add(stackFilter);
  const stackOptions: [string, string][] = [...[...stacks].sort().map((p): [string, string] => [p, p]), [NO_STACK, "Ohne Stack"]];
  const filtered = Boolean(hostFilter || stackFilter || stateFilter);
  const onlyOperate = scoped.length > 0 && scoped.every((s) => s.operate_only);

  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="font-semibold text-xl tracking-tight">Docker</h2>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Container suchen …"
          aria-label="Container suchen"
          className="w-56 px-2 py-1 text-sm rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
        />
        <button type="button" onClick={load} className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
          Aktualisieren
        </button>
        {deck().hasPermission("hosts.execute") && (services ?? []).length > 0 && !onlyOperate && (
          <button type="button" disabled={imagesChecking} onClick={() => void startImageCheck(false)}
            title="Fragt bei den Registries nach, ob es neuere Images gibt. Es wird nichts heruntergeladen oder neu gestartet."
            className="px-2 py-1 text-xs disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
            {imagesChecking ? "Prüfe Images …" : "Image-Updates prüfen"}
          </button>
        )}
      </div>
      {tabs}
      {(services ?? []).length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="filters">
          {(hostOptions.size > 1 || hostFilter) && (
            <FilterSelect label="Server" all="Alle Server" value={hostFilter} options={[...hostOptions]} testId="host-filter"
              onChange={(v) => updateUrl({ host: v, stack: null })} />
          )}
          {(stacks.size > 0 || stackFilter) && (
            <FilterSelect label="Stack" all="Alle Stacks" value={stackFilter} options={stackOptions} onChange={(v) => updateUrl({ stack: v })} />
          )}
          <FilterSelect label="Zustand" all="Alle Zustände" value={stateFilter} options={STATE_FILTERS.map((f) => [f.value, f.label])}
            onChange={(v) => updateUrl({ state: v })} />
          {filtered && (
            <button type="button" onClick={() => updateUrl({ host: null, stack: null, state: null })}
              className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
              Filter entfernen
            </button>
          )}
        </div>
      )}
      {error && <LoadError what="Der aktuelle Stand" error={error} onRetry={load} hint="Die Tabelle zeigt den letzten bekannten Stand." />}
      {message && <p className="mb-3 text-sm opacity-80">{message}</p>}
      {(imageSummary || imageMessage) && (
        <p className="mb-3 flex flex-wrap items-center gap-x-3 text-xs opacity-80" aria-live="polite" data-testid="image-summary">
          {imageSummary && <span>{imageSummary}</span>}
          {imageMessage && <span className="text-red-400">{imageMessage}</span>}
          {!imagesChecking && imageSummary && deck().hasPermission("hosts.execute") && (
            <button type="button" onClick={() => void startImageCheck(true)}
              title="Fragt ohne Zwischenspeicher noch einmal bei den Image-Quellen (Registries) nach. Sonst werden ihre Antworten 6 Stunden wiederverwendet. Docker Hub begrenzt die Zahl der Abfragen."
              className="underline opacity-70 hover:opacity-100">
              Neu abfragen
            </button>
          )}
        </p>
      )}
      {(services ?? []).length === 0 && <NoContainers />}
      {(services ?? []).length > 0 && byHost.size === 0 && (
        <p className="mb-3 text-sm opacity-70" data-testid="no-match">
          {needle ? "Kein Container passt zu deiner Suche." : "Für diesen Filter gibt es keine Container."}
        </p>
      )}
      {[...byHost.entries()].map(([host, entries]) => (
        <section key={host} className="mb-6 overflow-x-auto">
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <h3 className="text-sm font-medium opacity-60">{host}</h3>
            {entries[0]?.host_id && environments[entries[0].host_id] && (
              <span className="text-xs opacity-60" title={environmentText(environments[entries[0].host_id]).title} data-testid={`environment-${entries[0].host_id}`}>
                {environmentText(environments[entries[0].host_id]).text}
              </span>
            )}
            {entries[0]?.operate_only && entries[0].host_id && <OperateBadge hostId={entries[0].host_id} />}
            {entries[0]?.host_id && entries[0].state !== "error" && !entries[0].operate_only && deck().hasPermission("hosts.execute") && (
              <button type="button" onClick={() => setStorageFor(storageFor === entries[0].host_id ? null : entries[0].host_id!)}
                aria-expanded={storageFor === entries[0].host_id}
                className="px-2 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
                Docker-Speicher
              </button>
            )}
            {entries[0]?.host_id && !entries[0].operate_only && deck().hasPermission("hosts.execute") && (
              <BulkProposeButton
                hostId={entries[0].host_id}
                hostName={host}
                candidates={entries
                  .filter((s) => s.host_id && s.container && s.state !== "error" && (s.state === "running" || s.state === "restarting")
                    && !s.is_self && images?.data[s.id]?.status === "update" && images.data[s.id].apply?.mode !== "none")
                  .map((s) => ({ hostId: s.host_id!, container: s.container!, name: s.name }))}
                onDone={reloadImages}
              />
            )}
          </div>
          {entries[0]?.host_id && images?.hosts[entries[0].host_id]?.error && (
            <p className="mb-2 text-xs text-red-400" data-testid={`image-error-${entries[0].host_id}`}>
              Image-Prüfung: {images.hosts[entries[0].host_id].error}
            </p>
          )}
          {storageFor && storageFor === entries[0]?.host_id && (
            <div className="mb-3 bg-black/20 p-2 panel">
              <StoragePanel hostId={storageFor} />
            </div>
          )}
          {/* Am Handy (unter 768 px) wird jede Zeile zur Karte, nichts liegt außerhalb des Bildschirms. Die Spaltenköpfe
              entfallen, die Werte tragen ihre Beschriftung selbst (`data-label`). Die Rollen erhalten die Tabellen-Bedeutung
              für Vorlese-Programme, die sie bei `display: block` sonst verlieren. */}
          <table role="table" className="w-full text-sm max-md:block">
            <thead className="max-md:hidden">
              <tr className="border-b border-white/10 text-left text-xs opacity-60">
                <th className="py-1 w-1/4">Container</th>
                <th className="py-1 whitespace-nowrap">Zustand</th>
                <th className="py-1">Status</th>
                <th className="py-1 whitespace-nowrap">Image-Update</th>
                <th className="py-1 whitespace-nowrap">CPU</th>
                <th className="py-1 whitespace-nowrap">RAM</th>
                <th className="py-1">Aktionen</th>
              </tr>
            </thead>
            <tbody role="rowgroup" className="divide-y divide-white/5 max-md:block max-md:space-y-2 max-md:divide-y-0">
              {entries.map((s) => {
                const manageable = Boolean(s.host_id && s.container) && s.state !== "error";
                const isRunning = s.state === "running" || s.state === "restarting";
                const logsOpen = logsFor === s.id;
                const busy = (verb: Verb) => pending === `${s.id}:${verb}`;
                // Am Handy steht in der Karte kein einsames „–“: die Zelle erscheint erst mit einem Ergebnis.
                const imageShown = Boolean(images?.data[s.id]) || Boolean(s.host_id && images?.hosts[s.host_id]?.checking);
                return (
                  <Fragment key={s.id}>
                    <tr role="row" className={CARD_ROW} data-testid={`row-${s.id}`}>
                      <td role="cell" className="py-1.5 break-words max-md:w-full max-md:py-0 max-md:text-base">
                        {s.name}
                        {s.is_self && <span className="ml-1.5 rounded bg-white/10 px-1 text-[10px] opacity-70">Nodvard Deck</span>}
                        {s.compose_project && s.operate_only && (
                          <span className="ml-1.5 rounded bg-sky-500/15 px-1 text-[10px] text-sky-300" title="Docker-Compose-Projekt">{s.compose_project}</span>
                        )}
                        {s.compose_project && !s.operate_only && (
                          <button
                            type="button"
                            disabled={!deck().hasPermission("hosts.execute") || pending === `stack:${s.host_id}:${s.compose_project}`}
                            onClick={() => void restartStack(s)}
                            title="Docker-Compose-Projekt – klicken: ganzen Stack neu starten"
                            className="ml-1.5 rounded bg-sky-500/15 px-1 text-[10px] text-sky-300 hover:bg-sky-500/25 disabled:cursor-default disabled:hover:bg-sky-500/15"
                          >
                            {s.compose_project}
                          </button>
                        )}
                        {s.image && <span className="block text-xs opacity-50">{s.image}</span>}
                      </td>
                      <td role="cell" className="py-1.5 max-md:py-0">
                        <span className={`rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS[s.tone] ?? TONE_CLASS.neutral}`}>
                          {STATE_LABEL[s.state] ?? s.state}
                        </span>
                      </td>
                      <td role="cell" className="py-1.5 break-words opacity-70 max-md:min-w-0 max-md:flex-1 max-md:py-0 max-md:text-xs" title={s.status !== statusText(s.status) ? s.status : undefined}>
                        {statusText(s.status)}
                        {restarts[s.id] > 0 && (
                          <span className={`ml-1 whitespace-nowrap ${restartsAreAProblem(restarts[s.id], s.status) ? "text-amber-300" : ""}`}
                            data-testid={`restarts-${s.id}`} title="So oft hat Docker den Container nach seinem Ende von selbst wieder gestartet.">
                            · {restarts[s.id]}× neu gestartet
                          </span>
                        )}
                      </td>
                      <td role="cell" className={`py-1.5 text-xs max-md:w-full max-md:py-0 ${imageShown ? "" : "max-md:hidden"}`}>
                        <div data-testid={`image-${s.id}`}>
                          {manageable && <ImageUpdateBadge result={images?.data[s.id]} checking={Boolean(s.host_id && images?.hosts[s.host_id]?.checking)} running={isRunning}
                            operateOnly={s.operate_only} />}
                        </div>
                        {manageable && (
                          <ImageUpdateAction
                            entryId={s.id}
                            offer={isRunning && !s.is_self && !s.operate_only && images?.data[s.id]?.status === "update" && deck().hasPermission("hosts.execute")}
                            apply={images?.data[s.id]?.apply}
                            applying={images?.applying?.[s.id]}
                            applied={images?.applied?.[s.id]}
                            open={updateFor === s.id}
                            onToggle={() => setUpdateFor(updateFor === s.id ? null : s.id)}
                          />
                        )}
                      </td>
                      <td role="cell" data-label="CPU" className={`py-1.5 whitespace-nowrap text-xs opacity-80 max-md:py-0 ${CARD_LABEL}`} data-testid={`cpu-${s.id}`}>
                        {!statsLoaded ? (
                          <span title={MEASURING_HINT}>…</span>
                        ) : stats[s.id]?.cpu_percent != null ? (
                          `${stats[s.id].cpu_percent!.toFixed(1)} %`
                        ) : (
                          "–"
                        )}
                      </td>
                      <td role="cell" data-label="RAM" className={`py-1.5 whitespace-nowrap text-xs opacity-80 max-md:py-0 ${CARD_LABEL}`} data-testid={`mem-${s.id}`}>
                        {!statsLoaded ? (
                          <span title={MEASURING_HINT}>…</span>
                        ) : stats[s.id] ? (
                          stats[s.id].mem_used != null ? (
                            formatBytes(stats[s.id].mem_used!)
                          ) : (
                            <span title={MEM_UNKNOWN_HINT}>n. v.</span>
                          )
                        ) : (
                          "–"
                        )}
                      </td>
                      <td role="cell" className="py-1.5 max-md:w-full max-md:py-0">
                        <div className="flex flex-wrap gap-1.5">
                          {manageable && !isRunning && (
                            <button type="button" disabled={busy("start")} onClick={() => void trigger(s, "start")}
                              className={`rounded bg-emerald-500/20 px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40 ${TOUCH}`}>
                              {busy("start") ? "…" : "Starten"}
                            </button>
                          )}
                          {manageable && REMOVABLE_STATES.has(s.state) && !s.is_self && !s.operate_only && (
                            <button type="button" disabled={busy("remove")} onClick={() => void trigger(s, "remove")}
                              className={`rounded px-2 py-1 text-xs text-red-300 hover:bg-red-500/20 disabled:opacity-40 ${TOUCH}`}>
                              {busy("remove") ? "…" : "Löschen"}
                            </button>
                          )}
                          {manageable && isRunning && (
                            <button type="button" disabled={busy("restart")} onClick={() => void trigger(s, "restart")}
                              className={`px-2 py-1 text-xs disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition ${TOUCH}`}>
                              {busy("restart") ? "…" : "Neustart"}
                            </button>
                          )}
                          {manageable && isRunning && (
                            <button type="button" disabled={busy("stop") || s.is_self} onClick={() => void trigger(s, "stop")}
                              title={s.is_self ? "Das ist Nodvard Deck selbst – Stoppen nur direkt auf dem Server." : undefined}
                              className={`rounded bg-red-500/20 px-2 py-1 text-xs text-red-300 hover:bg-red-500/30 disabled:opacity-40 ${TOUCH}`}>
                              {busy("stop") ? "…" : "Stoppen"}
                            </button>
                          )}
                          {manageable && (
                            <button type="button" onClick={() => setDetailsFor(detailsFor === s.id ? null : s.id)} aria-expanded={detailsFor === s.id}
                              className={`rounded px-2 py-1 text-xs hover:bg-white/20 ${TOUCH} ${detailsFor === s.id ? "bg-white/20" : "bg-white/10"}`}>
                              Details
                            </button>
                          )}
                          {manageable && isRunning && !s.is_self && !s.operate_only && s.host_id && s.container && deck().hasPermission("hosts.execute") && (
                            // Shell im Container über das Terminal des Kerns (scope/ref, siehe shell.py).
                            <a href={consoleHref(s.host_id, s.container)}
                              className={`rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 ${TOUCH}`}>
                              Konsole
                            </a>
                          )}
                          {manageable && (
                            <button type="button" onClick={() => setLogsFor(logsOpen ? null : s.id)} aria-expanded={logsOpen}
                              className={`rounded px-2 py-1 text-xs hover:bg-white/20 ${TOUCH} ${logsOpen ? "bg-white/20" : "bg-white/10"}`}>
                              Logs
                            </button>
                          )}
                          <OpenLink url={s.url} />
                          {manageable && s.host_id && s.container && deck().hasPermission("apps.write") && (
                            <button type="button" onClick={() => setUrlFor(urlFor === s.id ? null : s.id)} aria-expanded={urlFor === s.id}
                              title={s.url_custom ? "Eigene Adresse – hier ändern oder die erkannte wieder nehmen." : "Adresse für „Öffnen“ selbst festlegen."}
                              className={`px-1 py-1 text-xs opacity-70 hover:opacity-100 hover:underline max-md:px-2 max-md:py-2`}>
                              {s.url_custom ? "Adresse (eigene)" : "Adresse"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {urlFor === s.id && s.host_id && s.container && (
                      <tr role="row" className="max-md:block">
                        <td role="cell" colSpan={7} className="bg-black/20 p-2 max-md:block">
                          <UrlPanel entry={s} onSaved={load} onClose={() => setUrlFor(null)} />
                        </td>
                      </tr>
                    )}
                    {detailsFor === s.id && (
                      <tr role="row" className="max-md:block">
                        <td role="cell" colSpan={7} className="bg-black/20 p-2 max-md:block">
                          <DetailsPanel entry={s} onChanged={(text) => { setDetailsFor(null); setMessage(text); load(); }} />
                        </td>
                      </tr>
                    )}
                    {updateFor === s.id && s.host_id && s.container && (
                      <tr role="row" className="max-md:block">
                        <td role="cell" colSpan={7} className="bg-black/20 p-2 max-md:block">
                          <UpdatePanel hostId={s.host_id} container={s.container} applying={images?.applying?.[s.id]}
                            onStarted={reloadImages} onFinished={load} onClose={() => setUpdateFor(null)} />
                        </td>
                      </tr>
                    )}
                    {logsOpen && (
                      <tr role="row" className="max-md:block">
                        <td role="cell" colSpan={7} className="bg-black/20 p-2 max-md:block">
                          <LogPanel entry={s} onClose={() => setLogsFor(null)} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

// Alter Name der Seite: Schon geöffnete Tabs fragen das Bundle noch damit an.
export const ServiceMatrixPage = ContainersPage;
