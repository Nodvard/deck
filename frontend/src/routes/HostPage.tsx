/**
 * Server-Seite (aehnlich wie in Plesk): alles zu EINEM Host an einer Stelle -- Zustand,
 * Schnellzugriff (Konsole/Terminal), Aktionen, Live-Auslastung, Werkzeuge &
 * Einstellungen, Dienste, Meldungen.
 *
 * Herstellerneutral: die Werkzeug-Kacheln kommen aus `GET /hosts/{id}/tools`
 * (Extensions melden sie per `ctx.ui.register_host_tool()` an), die Aktionen aus
 * `GET /hosts/{id}/actions` -- Aktionen mit Pflichtfeldern (`params_schema.required`)
 * bekommen ein kleines Formular statt eines Knopfs, der ins Leere liefe.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Monitor, Pencil, Server, SquareTerminal } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ButtonLink, EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import {
  type ActionOutcome,
  type ActionOutcomeSource,
  describeActionOutcome,
  isActionOutcomeSource,
  OUTCOME_TEXT_CLASS,
  type OutcomeTone,
} from "../lib/actionOutcome";
import {
  ActionWaitTimeout,
  GAVE_UP_WAITING_TEXT,
  isAbortError,
  isActionRunning,
  RUNNING_IN_BACKGROUND_TEXT,
  throwIfAborted,
  waitForAction,
} from "../lib/actions";
import { api, ApiError } from "../lib/api";
import { useExtensions } from "../lib/firstSteps";
import { useUnmountSignal } from "../lib/lifecycle";
import {
  formatBytes,
  formatUptime,
  hasConsole,
  hostHealth,
  hostStatusText,
  type HostOut,
  isNode,
  relativeTime,
  useHostMetrics,
  useOverview,
  useTerminalHosts,
} from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { confirmDialog } from "../state/dialogs";
import { AppTile, Ring } from "./Cockpit";
import { AccessCard } from "./settings/hosts/AccessCard";
import { DeleteHostButton } from "./settings/hosts/deleteHost";
import { HostHistory } from "./HostHistory";

interface ToolOut {
  ext_id: string;
  id: string;
  title: string;
  description: string | null;
  icon: string | null;
  category: string;
  href: string;
}

interface ActionSpecOut {
  action_type: string;
  label: string;
  description: string | null;
  default_risk: string;
  permissions: string[];
  confirm_text: string | null;
  command_field: string | null;
  params_schema: { properties?: Record<string, { type?: string; title?: string }>; required?: string[] } | null;
  source?: "host" | "global";
}

/** Felder, ohne die die Aktion nicht laufen kann: Pflichtfelder plus das Befehlsfeld
 * (Shell-Befehl & Co. haben kein params_schema, nur `command_field`). */
function inputFields(spec: ActionSpecOut): string[] {
  const fields = [...(spec.params_schema?.required ?? [])];
  if (spec.command_field && !fields.includes(spec.command_field)) fields.push(spec.command_field);
  return fields;
}

interface NotificationOut {
  id: string;
  ts: string;
  severity: string;
  title: string;
  payload?: Record<string, unknown>;
}

const CATEGORY_LABEL: Record<string, string> = {
  control: "Steuerung",
  monitoring: "Überwachung",
  services: "Dienste",
  data: "Daten & Sicherungen",
  settings: "Einstellungen",
};
const CATEGORY_ORDER = ["control", "monitoring", "services", "data", "settings"];
const KIND_LABEL: Record<string, string> = { vm: "Virtuelle Maschine", lxc: "Container", hypervisor: "Proxmox-Knoten", node: "Knoten" };

const RISK_STYLE: Record<string, string> = {
  low: "bg-white/10 hover:bg-white/15",
  medium: "bg-amber-500/15 text-amber-200 hover:bg-amber-500/25",
  high: "bg-red-500/15 text-red-200 hover:bg-red-500/25",
  critical: "bg-red-500/25 text-red-100 hover:bg-red-500/35",
};

const MESSAGE_CLASS: Record<OutcomeTone, string> = { ...OUTCOME_TEXT_CLASS, neutral: "text-white/80" };

function useHostData(hostId: string) {
  const host = useQuery({ queryKey: ["hosts", hostId], queryFn: () => api.get<HostOut>(`/hosts/${hostId}`) });
  const tools = useQuery({ queryKey: ["hosts", hostId, "tools"], queryFn: () => api.get<ToolOut[]>(`/hosts/${hostId}/tools`) });
  const actions = useQuery({ queryKey: ["hosts", hostId, "actions"], queryFn: () => api.get<ActionSpecOut[]>(`/hosts/${hostId}/actions`) });
  return { host, tools, actions };
}

/** Meldung einer Aktion an die Seite. `run` ist die Nummer aus `onStart`; ein
 * Zwischenstand (`interim`, "Laeuft im Hintergrund") ist kein Ergebnis. */
type ActionReport = (message: ActionOutcome, run: number, interim?: boolean) => void;
type SayFn = (message: ActionOutcome, interim?: boolean) => void;
/** Die Meldung einer Aktion auf der Seite; `done` false = Zwischenstand, es kommt noch etwas. */
type RunMessage = { run: number; outcome: ActionOutcome; done: boolean };
/** So viele Meldungen (die der letzten Aktionen) stehen hoechstens untereinander. */
const MAX_MESSAGES = 3;

function ActionControl({ hostId, spec, onStart, onDone }: { hostId: string; spec: ActionSpecOut; onStart: () => number; onDone: ActionReport }) {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const required = inputFields(spec);
  const [values, setValues] = useState<Record<string, string>>({});
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // Nachfragen zu einer Aktion im Hintergrund endet mit der Seite. Das Signal gibt
  // es schon vor der Freigabe-Antwort.
  const unmountSignal = useUnmountSignal();

  /** Laeuft die Aktion nach der Antwort noch (202 'executing'), Zwischenstand melden
   * und bis zum Ende nachfragen. */
  async function settle(source: ActionOutcomeSource, actionId: string, say: SayFn): Promise<ActionOutcomeSource> {
    if (!isActionRunning(source.status)) return source;
    const signal = unmountSignal();
    throwIfAborted(signal); // Seite schon verlassen, waehrend die Freigabe noch wartete
    say({ tone: "pending", text: `${spec.label}: ${RUNNING_IN_BACKGROUND_TEXT}` }, true);
    return waitForAction<ActionOutcomeSource>(actionId, { signal });
  }

  async function run() {
    if (spec.default_risk !== "low" || spec.confirm_text) {
      const ok = await confirmDialog(spec.confirm_text ?? `${spec.label}?`, { danger: spec.default_risk !== "low", confirmLabel: spec.label });
      // Waehrend die Rueckfrage offen war, kann man die Seite verlassen haben (Zurueck,
      // Strg+K, anderer Server): der globale Dialog bleibt dann offen. Wer jetzt noch
      // bestaetigt, soll nichts mehr in einem anderen Kontext ausloesen.
      if (!ok || unmountSignal().aborted) return;
    }
    setBusy(true);
    const runId = onStart();
    const say: SayFn = (message, interim) => onDone(message, runId, interim);
    // Deutscher Text samt Grund (result.error / Sperr-Begruendung) statt
    // des englischen Rohworts, siehe lib/actionOutcome.ts.
    const report = (source: ActionOutcomeSource) => {
      const outcome = describeActionOutcome(source);
      say({ tone: outcome.tone, text: `${spec.label}: ${outcome.text}` });
    };
    const closeForm = () => {
      setOpen(false);
      setValues({});
    };
    try {
      const action = await api.post<ActionOutcomeSource & { id: string; status: string; risk: string }>(`/hosts/${hostId}/actions/${spec.action_type}`, {
        payload: values, reason: `${spec.label} über die Server-Seite`,
      });
      if (action.status === "proposed" && hasPermission(`actions.approve:${action.risk}`)) {
        const approved = await api.post<ActionOutcomeSource>(`/actions/${action.id}/approve`);
        closeForm();
        report(await settle(approved, action.id, say));
      } else if (action.status === "proposed") {
        closeForm();
        say({ tone: "pending", text: `${spec.label}: vorgeschlagen – Freigabe unter „Aktionen“ nötig.` });
      } else {
        closeForm();
        report(await settle(action, action.id, say));
      }
    } catch (err) {
      // Seite verlassen: nichts mehr melden.
      if (isAbortError(err)) return;
      if (err instanceof ActionWaitTimeout) say({ tone: "pending", text: `${spec.label}: ${GAVE_UP_WAITING_TEXT}` });
      // Gate-Sperre (Sperrliste, Flap-Schutz): 403 mit ActionOut als Body.
      else if (err instanceof ApiError && isActionOutcomeSource(err.detail)) report(err.detail);
      else say({ tone: "error", text: `${spec.label}: Fehler – ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  const style = RISK_STYLE[spec.default_risk] ?? RISK_STYLE.low;
  if (required.length === 0) {
    return (
      <button type="button" disabled={busy} onClick={() => void run()} className={`rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${style}`}>
        {busy ? "…" : spec.label}
      </button>
    );
  }
  return (
    <span className="inline-flex flex-col gap-1.5">
      <button type="button" onClick={() => setOpen((v) => !v)} className={`rounded-lg px-3 py-1.5 text-xs font-medium ${style}`}>
        {spec.label} …
      </button>
      {open && (
        <form
          className="panel flex flex-wrap items-end gap-2 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
        >
          {required.map((key) => (
            <label key={key} className="text-[11px] text-white/60">
              {spec.params_schema?.properties?.[key]?.title ?? key}
              <input
                required
                aria-label={spec.params_schema?.properties?.[key]?.title ?? key}
                value={values[key] ?? ""}
                onChange={(e) => setValues({ ...values, [key]: e.target.value })}
                className="mt-0.5 block rounded-md border border-white/10 bg-black/30 px-2 py-1 text-xs text-white"
              />
            </label>
          ))}
          <button type="submit" disabled={busy} className="accent-gradient rounded-md px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50">
            {busy ? "…" : "Ausführen"}
          </button>
        </form>
      )}
    </span>
  );
}

/** Wer misst die Auslastung? Bei Proxmox-Gästen Proxmox selbst, sonst das Modul „System“ per SSH. Fehlt
 * beides (404), erklärt dieser Hinweis, was zu tun ist, statt den Abschnitt wortlos wegzulassen. */
function MetricsHint({ host, canWriteHosts, canManageExtensions }: { host: HostOut; canWriteHosts: boolean; canManageExtensions: boolean }) {
  if (host.os_family !== "linux") return null; // Windows: dafür gibt es (noch) keine Messung, kein Hinweis nötig
  const noAccess = !host.credential;
  return (
    <div className="panel p-4 text-sm text-white/70" data-testid="host-metrics-hint">
      <p className="font-medium text-white/85">Noch keine Auslastung</p>
      <p className="mt-1 text-xs text-white/55">
        {noAccess
          ? "CPU, Arbeitsspeicher und Platte misst Nodvard Deck per SSH. Dafür braucht der Server einen SSH-Zugang."
          : "CPU, Arbeitsspeicher und Platte misst das Modul „System“ per SSH. Schalte es unter Einstellungen → Erweiterungen ein, dann erscheinen hier Auslastung und Verlauf."}
      </p>
      {((noAccess && canWriteHosts) || (!noAccess && canManageExtensions)) && (
        <div className="mt-2">
          {noAccess
            ? <ButtonLink to={`/settings/hosts/${host.id}`} variant="secondary">SSH-Zugang einrichten</ButtonLink>
            : <ButtonLink to="/settings/extensions" variant="secondary">Module ansehen</ButtonLink>}
        </div>
      )}
    </div>
  );
}

function Metrics({ host, canWriteHosts, canManageExtensions }: { host: HostOut; canWriteHosts: boolean; canManageExtensions: boolean }) {
  const { data, isError, error } = useHostMetrics(host.id);
  const v = data?.values ?? {};
  if (isError) {
    return error instanceof ApiError && error.status === 404
      ? <MetricsHint host={host} canWriteHosts={canWriteHosts} canManageExtensions={canManageExtensions} />
      : null;
  }
  if (!data && !isNode(host) && host.kind !== "vm" && host.kind !== "lxc") return null;
  const memPct = v.mem_total_bytes ? (100 * (v.mem_used_bytes ?? 0)) / v.mem_total_bytes : null;
  return (
    <div className="panel flex flex-wrap items-center justify-around gap-6 p-5" data-testid="host-metrics">
      <Ring value={v.cpu_percent ?? null} label="CPU" />
      <Ring value={memPct} label="RAM" />
      <div className="text-center">
        <p className="text-lg font-semibold tabular-nums">{v.mem_used_bytes ? formatBytes(v.mem_used_bytes) : "–"}</p>
        <p className="text-[11px] text-white/50">von {v.mem_total_bytes ? formatBytes(v.mem_total_bytes) : "–"} RAM</p>
      </div>
      <div className="text-center">
        <p className="text-lg font-semibold">{formatUptime(v.uptime_s)}</p>
        <p className="text-[11px] text-white/50">läuft seit</p>
      </div>
    </div>
  );
}

function TagEditor({ hostId, tags, managed, onSaved }: { hostId: string; tags: string[]; managed: string[]; onSaved: () => void }) {
  // Markierungen einer Erweiterung lassen sich von Hand nicht aendern -- sie stehen nur zur Info da.
  const editable = tags.filter((t) => !managed.includes(t));
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const list = text.split(/[,\s]+/).map((t) => t.trim().toLowerCase()).filter(Boolean);
      await api.patch(`/hosts/${hostId}`, { tags: list });
      setOpen(false);
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Speichern fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => { setText(editable.join(", ")); setError(null); setOpen(true); }}
        className="mt-1.5 text-[11px] text-white/45 underline-offset-2 hover:text-white hover:underline"
      >
        Markierungen bearbeiten
      </button>
    );
  }
  return (
    <div className="mt-2 max-w-md space-y-1.5" data-testid="tag-editor">
      <input
        aria-label="Markierungen"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="docker, gameserver"
        className="w-full rounded-lg bg-white/10 px-3 py-1.5 text-xs outline-none focus:ring-1 focus:ring-white/30"
      />
      <p className="text-[11px] text-white/45">
        Mit Komma trennen, z. B. docker (Service-Matrix), gameserver (Gameserver). Markierungen von Erweiterungen bleiben erhalten
        {managed.length > 0 ? ` (${managed.join(", ")}) und lassen sich nicht ändern` : ""}.
      </p>
      {error && <p className="text-[11px] text-red-400">{error}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={busy} onClick={() => void save()} className="rounded-lg bg-white/15 px-3 py-1 text-xs font-medium hover:bg-white/20 disabled:opacity-50">
          {busy ? "Speichere …" : "Speichern"}
        </button>
        <button type="button" disabled={busy} onClick={() => setOpen(false)} className="rounded-lg px-3 py-1 text-xs text-white/60 hover:text-white">
          Abbrechen
        </button>
      </div>
    </div>
  );
}

/** Die Route `/hosts/:hostId` bleibt beim Wechsel zu einem anderen Server dieselbe --
 * ohne `key` behielten Meldung, "Beschaeftigt"-Merker und laufende Nachfragen des alten
 * Servers ihren Zustand und tauchten beim neuen auf. */
export function HostPage() {
  const { hostId = "" } = useParams();
  return <HostView key={hostId} hostId={hostId} />;
}

function HostView({ hostId }: { hostId: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [deleteMessage, setDeleteMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const { host, tools, actions } = useHostData(hostId);
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canExecute = useAuthStore((s) => s.hasPermission("hosts.execute"));
  const canReadNotifications = useAuthStore((s) => s.hasPermission("notifications.read"));
  const canWriteHosts = useAuthStore((s) => s.hasPermission("hosts.write"));
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  const { data: terminalHosts } = useTerminalHosts(canExecute);
  const { data: overview } = useOverview();
  const { data: extensions } = useExtensions();
  const notifications = useQuery({
    queryKey: ["notifications", "host", hostId],
    queryFn: () => api.get<NotificationOut[]>("/notifications?limit=100"),
    enabled: canReadNotifications,
  });
  // Jede gestartete Aktion bekommt eine Nummer und ihre eigene Meldung. Die Freigabe
  // wartet bis zu 20 s auf die Antwort, in der Zeit sind die anderen Knoepfe weiter
  // frei: das spaete Ergebnis der aelteren Aktion darf die Meldung der neueren (z. B.
  // einen Fehlschlag) nicht ersetzen -- die Server-Seite hat keine Statusspalte, wo man
  // ihn nachlesen koennte.
  const [messages, setMessages] = useState<RunMessage[]>([]);
  const startedRuns = useRef(0);

  function onActionStart(): number {
    startedRuns.current += 1;
    // Neue Aktion: fertige Meldungen frueherer Aktionen verschwinden, die Meldungen noch laufender Aktionen bleiben.
    setMessages((prev) => prev.filter((m) => !m.done));
    return startedRuns.current;
  }

  const onActionDone: ActionReport = (outcome, run, interim = false) => {
    setMessages((prev) => [...prev.filter((m) => m.run !== run), { run, outcome, done: !interim }].sort((a, b) => a.run - b.run).slice(-MAX_MESSAGES));
    void queryClient.invalidateQueries({ queryKey: ["hosts"] });
  };

  if (host.isLoading) return <p className="p-6 text-sm opacity-60">Lade Server …</p>;
  if (host.error || !host.data) return <p className="p-6 text-sm text-red-400">Server nicht gefunden.</p>;

  const h = host.data;
  const health = hostHealth(h.status);
  // Von einem Modul eingelesen (z. B. Proxmox) und das Modul ist inzwischen aus: der Zustand wird nicht mehr nachgeführt.
  const providerExt = h.provider_ext_id ? extensions?.find((e) => e.id === h.provider_ext_id) : undefined;
  const providerOff = providerExt !== undefined && providerExt.state !== "enabled";
  const canTerminal = (terminalHosts ?? []).includes(h.id);
  const services = (overview?.services ?? []).filter((s) => s.host_id === h.id);
  const hostNotes = (notifications.data ?? []).filter((n) => n.payload?.host_id === h.id).slice(0, 8);
  const grouped = CATEGORY_ORDER.map((category) => ({
    category,
    items: [
      ...(category === "control" && hasConsole(h) && health === "online"
        ? [{ key: "console", title: "Konsole", description: "Bildschirm des Gasts im Browser", icon: "monitor", href: `/console/${h.id}` }]
        : []),
      ...(category === "control" && canTerminal
        ? [{ key: "terminal", title: "Terminal", description: "SSH-Sitzung auf diesem Server", icon: "terminal", href: `/terminal?host=${encodeURIComponent(h.id)}` }]
        : []),
      ...(tools.data ?? []).filter((t) => t.category === category).map((t) => ({ key: `${t.ext_id}:${t.id}`, title: t.title, description: t.description, icon: t.icon, href: t.href })),
    ],
  })).filter((g) => g.items.length > 0);
  // Wie der Kern beim Ausloesen: ohne eigene Rechte-Angabe gilt hosts.execute.
  const allowed = (actions.data ?? [])
    .filter((a) => (a.permissions.length ? a.permissions : ["hosts.execute"]).every((p) => hasPermission(p)))
    // Pflichtfelder vom Typ Objekt/Liste (z. B. "Hardware ändern") haben ihr eigenes
    // Formular in der Extension -- hier gaebe es nur ein Textfeld fuer rohes JSON.
    .filter((a) => inputFields(a).every((f) => ["string", "number", "integer", undefined].includes(a.params_schema?.properties?.[f]?.type)));
  // Knoepfe: was der Host selbst anbietet und alles ohne Eingabe. Allgemeine Aktionen
  // mit Eingabefeld (Shell-Befehl, Container-Name ...) liegen eingeklappt darunter.
  const quickActions = allowed.filter((a) => a.source === "host" || inputFields(a).length === 0);
  const moreActions = allowed.filter((a) => !quickActions.includes(a));

  return (
    <div className="p-4 sm:p-6">
      <Link to="/" className="mb-4 inline-flex items-center gap-1 text-xs text-white/50 hover:text-white">
        <ArrowLeft size={13} /> Übersicht
      </Link>

      <div className="panel relative isolate overflow-hidden p-5">
        <div className="nodvard-deck-glow pointer-events-none absolute inset-0 -z-10 opacity-70" />
        <div className="flex flex-wrap items-center gap-4">
          <span className="accent-gradient grid h-12 w-12 place-items-center rounded-xl text-white shadow-lg shadow-black/40">
            {hasConsole(h) ? <Monitor size={22} /> : <Server size={22} />}
          </span>
          <div className="min-w-0">
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
              <span className={`status-dot ${health}`} />
              {h.display_name}
            </h1>
            <p className="text-sm text-white/55">
              {KIND_LABEL[h.kind ?? ""] ?? "Server"} · {h.address} · {hostStatusText(h)}
            </p>
            {h.tags.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {h.tags.map((tag) => <span key={tag} className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-white/70">{tag}</span>)}
              </div>
            )}
            {canWriteHosts && (
              <TagEditor
                key={h.tags.join(",")}
                hostId={h.id}
                tags={h.tags}
                managed={h.managed_tags ?? []}
                onSaved={() => void queryClient.invalidateQueries({ queryKey: ["hosts"] })}
              />
            )}
          </div>
          <div className="ml-auto flex flex-wrap gap-2">
            {hasConsole(h) && health === "online" && (
              <Link to={`/console/${h.id}`} className="flex items-center gap-1.5 rounded-lg bg-white/10 px-3 py-1.5 text-xs font-medium hover:bg-white/15">
                <Monitor size={14} /> Konsole
              </Link>
            )}
            {canTerminal && (
              <Link to={`/terminal?host=${encodeURIComponent(h.id)}`} className="flex items-center gap-1.5 rounded-lg bg-white/10 px-3 py-1.5 text-xs font-medium hover:bg-white/15">
                <SquareTerminal size={14} /> Terminal
              </Link>
            )}
            {canWriteHosts && (
              <>
                <Link to={`/settings/hosts/${h.id}`} className="flex items-center gap-1.5 rounded-lg bg-white/10 px-3 py-1.5 text-xs font-medium hover:bg-white/15">
                  <Pencil size={14} /> Bearbeiten
                </Link>
                <DeleteHostButton host={h} label="Löschen" onDeleted={() => navigate("/")} onMessage={setDeleteMessage} />
              </>
            )}
          </div>
        </div>
        {quickActions.length > 0 && (
          <div className="mt-4 flex flex-wrap items-start gap-2 border-t border-white/[0.06] pt-4" data-testid="host-actions">
            {quickActions.map((spec) => (
              <ActionControl key={spec.action_type} hostId={h.id} spec={spec} onStart={onActionStart} onDone={onActionDone} />
            ))}
          </div>
        )}
        {moreActions.length > 0 && (
          <details className="mt-3" data-testid="host-more-actions">
            <summary className="cursor-pointer text-xs text-white/55 hover:text-white">Weitere Aktionen ({moreActions.length})</summary>
            <div className="mt-2 flex flex-wrap items-start gap-2">
              {moreActions.map((spec) => (
                <ActionControl key={spec.action_type} hostId={h.id} spec={spec} onStart={onActionStart} onDone={onActionDone} />
              ))}
            </div>
          </details>
        )}
        {messages.length > 0 && (
          <div className="mt-3 space-y-1">
            {messages.map((m) => <p key={m.run} className={`text-sm ${MESSAGE_CLASS[m.outcome.tone]}`} role="status">{m.outcome.text}</p>)}
          </div>
        )}
      </div>

      {deleteMessage && <p role="alert" className="mt-3 text-sm text-red-300">{deleteMessage.text}</p>}

      {providerOff && (
        <p role="status" data-testid="host-provider-off" className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/[0.07] px-4 py-3 text-sm text-amber-100/90">
          Dieser Server wurde vom Modul „{providerExt?.name ?? h.provider_ext_id}“ eingelesen, und das läuft gerade nicht (ausgeschaltet oder gestört). Sein Zustand und seine
          Aktionen werden nicht mehr nachgeführt{canWriteHosts ? "; wenn du ihn nicht mehr brauchst, kannst du ihn oben löschen" : ""}.
        </p>
      )}

      {canWriteHosts && <AccessCard host={h} />}

      <div className="mt-4">
        <Metrics host={h} canWriteHosts={canWriteHosts} canManageExtensions={canManageExtensions} />
      </div>

      <HostHistory hostId={h.id} host={h} canCheck={canWriteHosts} />

      <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Werkzeuge &amp; Einstellungen</h2>
      {grouped.length === 0 && (
        <EmptyState
          icon="boxes"
          testId="host-no-tools"
          title="Noch keine Werkzeuge für diesen Server"
          text={
            canManageExtensions
              ? "Werkzeuge bringen die Module mit, zum Beispiel für Updates, Container oder Skripte. Schalte unter Einstellungen → Erweiterungen Module ein, dann erscheinen sie hier."
              : "Werkzeuge bringen die Module mit. Sobald ein Administrator passende Module einschaltet, erscheinen sie hier."
          }
          action={canManageExtensions ? <ButtonLink to="/settings/extensions">Module ansehen</ButtonLink> : undefined}
        />
      )}
      <div className="grid gap-6 lg:grid-cols-2" data-testid="host-tools">
        {grouped.map((group) => (
          <div key={group.category}>
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-white/45">{CATEGORY_LABEL[group.category] ?? group.category}</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {group.items.map((item) => (
                <Link key={item.key} to={item.href} className="panel panel-hover flex items-start gap-3 p-3">
                  <span className="accent-soft grid h-9 w-9 flex-none place-items-center rounded-lg"><Icon name={item.icon} size={17} /></span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{item.title}</span>
                    {item.description && <span className="block text-[11px] text-white/50">{item.description}</span>}
                  </span>
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>

      {services.length > 0 && (
        <>
          <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Dienste auf diesem Server</h2>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" data-testid="host-services">
            {services.map((s) => <AppTile key={s.id} service={s} />)}
          </div>
        </>
      )}

      {hostNotes.length > 0 && (
        <>
          <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Meldungen</h2>
          <ul className="panel divide-y divide-white/[0.06]" data-testid="host-notifications">
            {hostNotes.map((n) => (
              <li key={n.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span className={`status-dot ${n.severity === "critical" ? "problem" : n.severity === "warning" ? "unknown" : "online"}`} />
                <span className="min-w-0 flex-1 break-words">{n.title}</span>
                <span className="text-[11px] text-white/45">{relativeTime(n.ts)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
