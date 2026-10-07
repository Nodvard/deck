/**
 * Die Skript-Seite -- PageSpec `component=
 * "ScriptsPage"` (siehe nodvard_deck_ext_scripts/__init__.py).
 *
 * **Ehrlich abgegrenzt:** ein einfaches `<textarea>` statt CodeMirror 6 (docs/02
 * Paragraph 6 nennt woertlich CodeMirror) -- ein Extension-Bundle importiert nur
 * React/ReactDOM ueber den bestehenden Import-Map-Shim (siehe ProxmoxNodePage.tsx),
 * ein Editor-Paket dafuer haette das Kern-Frontend-`package.json` erweitert, nur fuer
 * diese eine Seite. Dieselbe Abwaegung wie die hand-gerollte SVG-`ChartView` statt
 * einer Charting-Bibliothek.
 *
 * Lauf-Historie/"Jetzt ausfuehren ueber den Zeitplan" laeuft bewusst NICHT ueber einen
 * eigenen Endpunkt dieser Extension, sondern direkt ueber die bestehenden Kern-
 * Endpunkte `GET /jobs?ext_id=scripts` und `GET /jobs/{id}/runs`, verknuepft ueber
 * `ext_job_key` (== `job_id`-Feld, das diese Extension pro Skript zurueckgibt) -- siehe
 * `__init__.py`s Modul-Docstring fuer die Begruendung.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { SchedulePicker, describeSchedule } from "../../../../frontend/src/components/SchedulePicker";
import { isActionRunning, waitForAction } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { Badge, Button, Card, EmptyState, Field, Icon, Notice, Page, inputClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

export { describeSchedule };

/** Ein Zielserver einer Dauerfreigabe mit dem Konto, unter dem das Dashboard sich anmeldet. */
interface StandingHost {
  id: string;
  name: string;
  account: string | null;
  address?: string | null;
  /** SSH-Port des Zugangs (fehlt bei älteren Kernen). */
  port?: number | null;
}

/** Dauerfreigabe eines Skripts (siehe standing.py im Backend). */
interface StandingOut {
  /** false: gilt nicht mehr (`problem`), erlischt beim nächsten Lauf. */
  active: boolean;
  problem?: string | null;
  granted_by_label: string;
  granted_at: string;
  hosts: StandingHost[];
  /** Neue Zielserver, die beim nächsten Lauf normal freigegeben werden müssen. */
  new_hosts?: string[];
}

interface ScriptOut {
  id: string;
  name: string;
  description: string;
  content: string;
  params_schema: Record<string, { type?: string; label?: string; default?: string }>;
  target: { kind?: string; host_id?: string | null; group_id?: string | null };
  schedule: string | null;
  enabled: boolean;
  job_id: string;
  /** Fingerabdruck von Inhalt, Parametern, Ziel und Zeitplan (Backend). */
  fingerprint?: string;
  standing_approval?: StandingOut | null;
  /** Server, die eine Dauerfreigabe jetzt decken würde (nur aktive Skripte mit Zeitplan). */
  standing_preview?: StandingHost[] | null;
  /** Fingerabdruck dieser Liste – wird beim Erteilen mitgeschickt. */
  targets_fingerprint?: string | null;
}

interface JobOut {
  id: string;
  ext_job_key: string | null;
  next_run_at: string | null;
  enabled: boolean;
}

interface ExecutionOut {
  action_id: string;
  status: string;
  host_id: string | null;
  host_name: string | null;
  proposed_by: string;
  /** Name des Auslösers (Nutzername, Erweiterung ...); fehlt -> `proposed_by` roh. */
  proposed_by_label?: string | null;
  created_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  output: string;
  error: string;
  duration_ms: number | null;
  /** Lief ohne Klick über die Dauerfreigabe; `reason` nennt, von wem und wann. */
  standing_approval?: boolean;
  reason?: string;
}

const EXEC_STATUS: Record<string, { label: string; tone: Tone }> = {
  proposed: { label: "wartet auf Freigabe", tone: "warn" },
  executing: { label: "läuft", tone: "info" },
  succeeded: { label: "erfolgreich", tone: "good" },
  failed: { label: "fehlgeschlagen", tone: "bad" },
  denied: { label: "abgelehnt", tone: "bad" },
  dismissed: { label: "verworfen", tone: "neutral" },
  expired: { label: "abgelaufen", tone: "neutral" },
};

function slugify(name: string): string {
  return name.toLowerCase()
    .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}

/** Wer den Lauf ausgelöst hat: der Name vom Backend statt „user/<uuid>“; ein Lauf, den die
 * Erweiterung selbst vorgeschlagen hat, ist ein geplanter. */
function triggeredBy(e: ExecutionOut): string {
  const name = e.proposed_by_label || e.proposed_by;
  if (e.standing_approval) return "Zeitplan · ohne Klick (Dauerfreigabe)";
  return e.proposed_by.startsWith("extension/") ? `Zeitplan · ${name}` : name;
}

/** Die Laeufe eines Skripts mit echter Ausgabe (`GET /scripts/{id}/runs`) -- vorher
 * sah man nach "Jetzt ausführen" nie, was das Skript ausgegeben hat. */
export function Executions({ items }: { items: ExecutionOut[] }): JSX.Element {
  if (items.length === 0) return <p className="text-sm text-white/45">Noch nicht ausgeführt.</p>;
  return (
    <ul className="space-y-2" data-testid="executions">
      {items.map((e) => {
        const st = EXEC_STATUS[e.status] ?? { label: e.status, tone: "neutral" as Tone };
        const hasOutput = Boolean(e.output || e.error);
        return (
          <li key={e.action_id} className="rounded-lg border border-white/[0.08] bg-black/15 text-sm">
            <details open={e === items[0] && hasOutput}>
              <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2">
                <Badge tone={st.tone}>{st.label}</Badge>
                <span className="font-medium">{e.host_name ?? "–"}</span>
                {e.exit_code != null && <span className="text-xs text-white/45">Exit {e.exit_code}</span>}
                {e.duration_ms != null && <span className="text-xs text-white/45">{(e.duration_ms / 1000).toFixed(1)} s</span>}
                {!hasOutput && e.status === "failed" && <span className="text-xs text-red-300">ohne Fehlermeldung</span>}
                <span className="ml-auto text-xs text-white/40" title={e.standing_approval ? e.reason : e.proposed_by}>
                  {e.created_at ? new Date(e.created_at).toLocaleString("de-DE") : ""} · {triggeredBy(e)}
                </span>
              </summary>
              <div className="border-t border-white/[0.06] px-3 py-2">
                {hasOutput ? (
                  <>
                    {e.output && <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-black/50 p-2.5 font-mono text-[11px] text-white/80">{e.output}</pre>}
                    {e.error && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-red-950/40 p-2.5 font-mono text-[11px] text-red-200">{e.error}</pre>}
                  </>
                ) : (
                  <p className="text-xs text-white/45">{e.status === "proposed" ? "Wird erst nach der Freigabe ausgeführt." : "Keine Ausgabe."}</p>
                )}
              </div>
            </details>
          </li>
        );
      })}
    </ul>
  );
}

interface RunOut {
  id: string;
  status: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
}

interface HostRow {
  id: string;
  name: string;
  display_name: string;
}

/** Ein Eintrag aus `POST /scripts/{id}/run` (siehe `run_script()` im Backend). */
interface RunResult {
  host_id?: string;
  host_name?: string;
  action_id?: string;
  status?: string;
  error?: string;
  skipped?: string;
}

/** Endstand eines Laufs nach der Freigabe, `reason` nur bei einem Fehlschlag. */
interface RunVerdict {
  status: string;
  reason?: string;
}

/** Kurzer Grund fuer die Meldung: Exit-Code und die erste Zeile der Fehlerausgabe --
 * die ganze Ausgabe steht darunter unter „Ausführungen“. */
function failureReason(result: { exit_code?: number | null; error?: string | null } | undefined): string | undefined {
  const line = (result?.error ?? "").split("\n").map((l) => l.trim()).find(Boolean);
  const short = line && line.length > 80 ? `${line.slice(0, 79)}…` : line;
  return [result?.exit_code != null ? `Exit ${result.exit_code}` : "", short ?? ""].filter(Boolean).join(": ") || undefined;
}

/** Gibt einen Vorschlag frei und liefert, wie der Lauf ausgegangen ist. Die Freigabe
 * wartet hoechstens etwa 20 s; laeuft das Skript laenger, kommt 202 mit 'executing'
 * zurueck (zaehlt als "laeuft noch", danach fragt `finishRun` nach). Bei mehreren Zielen
 * gilt `?wait=0`: die Freigabe startet den Lauf und kommt sofort zurueck, auch kurze
 * Laeufe melden sich dann erst nach etwa 3 s. Sonst hinge der Knopf bei "Alle Server"
 * N x 20 s, und das Skript startete auf dem letzten Server erst Minuten nach dem ersten.
 * Bei einem einzigen Ziel bleibt es bei der Wartezeit: ein kurzes Skript liefert sein
 * Ergebnis gleich mit. */
async function approveRun(actionId: string, immediate: boolean): Promise<RunVerdict> {
  try {
    const res = await authedFetch(`/actions/${actionId}/approve${immediate ? "?wait=0" : ""}`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (res.status === 409) return { status: "failed", reason: /'expired'/.test(body.detail ?? "") ? "Vorschlag abgelaufen" : "schon anderweitig bearbeitet" };
    if (res.status === 403) return { status: "failed", reason: "keine Berechtigung zum Freigeben" };
    if (!res.ok) return { status: "failed", reason: errorFromBody(body, res.status) };
    return { status: body.status ?? "", reason: failureReason(body.result) };
  } catch (err) {
    return { status: "failed", reason: err instanceof Error ? err.message : String(err) };
  }
}

/** Fragt einen noch laufenden Lauf ab, bis er fertig ist (alle 3 s, hoechstens eine
 * Stunde). Bleibt er so lange oder verlaesst man die Seite, gilt er weiter als laufend;
 * kann der Stand nicht abgerufen werden (Aktion weg, keine Rechte), ist das ein
 * eigener Fehler statt eines "laeuft noch", das nie endet. */
async function finishRun(actionId: string, signal?: AbortSignal): Promise<RunVerdict> {
  try {
    const state = await waitForAction(actionId, {}, { signal });
    return { status: state.status ?? "executing", reason: failureReason(state.result ?? undefined) };
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return { status: "executing" };
    return { status: "failed", reason: `Stand nicht abrufbar: ${err instanceof Error ? err.message : String(err)}` };
  }
}

interface RunOutcome {
  succeeded: number;
  running: number;
  waiting: number;
  failed: string[];
}

function countOutcome(outcome: RunOutcome, host: string, verdict: RunVerdict): void {
  if (verdict.status === "succeeded") outcome.succeeded += 1;
  else if (isActionRunning(verdict.status)) outcome.running += 1;
  else if (verdict.status === "proposed") outcome.waiting += 1;
  else {
    // "failed" ohne Grund: die Einzelheiten stehen darunter unter „Ausführungen“.
    let reason = verdict.reason;
    if (!reason && verdict.status === "denied") reason = "von einer Schutzregel blockiert";
    else if (!reason && verdict.status !== "failed") reason = EXEC_STATUS[verdict.status]?.label ?? verdict.status;
    outcome.failed.push(reason ? `${host} (${reason})` : host);
  }
}

/** Die Meldung unter dem Knopf aus dem Stand aller Ziele. */
function describeRun(outcome: RunOutcome, skipped: string[]): string {
  return [
    outcome.succeeded ? `${outcome.succeeded} Ziel(e) ausgeführt` : "",
    outcome.running ? `${outcome.running} laufen noch` : "",
    outcome.waiting ? `${outcome.waiting} warten auf Freigabe unter „Aktionen“` : "",
    outcome.failed.length ? `${outcome.failed.length} fehlgeschlagen: ${outcome.failed.join(", ")}` : "",
    skipped.length ? `${skipped.length} übersprungen: ${skipped.join(", ")}` : "",
  ].filter(Boolean).join(" · ") || "Keine Ziele.";
}

function emptyDraft(hostId: string | null = null): ScriptOut {
  return {
    id: "", name: "", description: "", content: "#!/bin/sh\n",
    params_schema: {}, target: { kind: "host", host_id: hostId }, schedule: null, enabled: true, job_id: "",
  };
}

/** „Einer Gruppe“ ohne Auswahl wuerde sonst auf allen Servern laufen -- ohne
 * Auswahl wird nicht gespeichert (das Backend lehnt es ebenso mit 422 ab). */
function targetHint(target: ScriptOut["target"]): string | null {
  if (target.kind === "group" && !target.group_id) return "Bitte zuerst eine Gruppe wählen.";
  if ((target.kind ?? "host") === "host" && !target.host_id) return "Bitte zuerst einen Server wählen.";
  return null;
}

/** Die Teile, deren Änderung eine Dauerfreigabe aufhebt (wie im Backend). */
function approvalRelevant(s: ScriptOut): string {
  return JSON.stringify([s.content, s.params_schema, s.target, s.schedule || null]);
}

/** „pve1 (als root, 192.168.2.10)“ – was die Freigabe für einen Server festhält; ein
 * ungewöhnlicher SSH-Port steht dabei („…, Port 2222“), der Standard 22 nicht. */
export function describeStandingHost(h: StandingHost): string {
  const port = h.port != null && h.port !== 22 ? `Port ${h.port}` : null;
  const details = [`als ${h.account ?? "kein Konto"}`, h.address, port].filter(Boolean).join(", ");
  return `${h.name} (${details})`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("de-DE");
}

/** Schalter „Ohne Freigabe nach Zeitplan“ mit Stand der Dauerfreigabe. Erteilen und
 * Zurückziehen prüft das Backend selbst (nur Owner/Admin); hier nur die Anzeige dazu. */
export function StandingApprovalPanel({
  script, dirty, canGrant, busy, onGrant, onRevoke,
}: {
  script: ScriptOut;
  dirty: boolean;
  canGrant: boolean;
  busy: boolean;
  onGrant: () => void;
  onRevoke: () => void;
}): JSX.Element {
  const standing = script.standing_approval ?? null;
  const on = Boolean(standing);
  const blocked = !canGrant || busy || (!on && (dirty || !script.enabled));
  return (
    <div className="mt-3 rounded-lg border border-white/[0.08] bg-black/15 p-3 text-sm" data-testid="standing-approval">
      <label className="flex items-start gap-2.5">
        <input
          type="checkbox"
          className="mt-1"
          aria-label="Ohne Freigabe nach Zeitplan"
          checked={on}
          disabled={blocked}
          onChange={() => (on ? onRevoke() : onGrant())}
        />
        <span>
          <span className="font-medium">Ohne Freigabe nach Zeitplan</span>
          <span className="mt-0.5 block text-xs text-white/55">
            Läuft nach Zeitplan ohne Freigabe, solange du das Skript nicht änderst. Jede Änderung an Inhalt,
            Parametern, Ziel oder Zeitplan hebt das auf, dann fragt das Dashboard wieder nach. Jeder Lauf steht
            weiter unter „Aktionen“.
          </span>
        </span>
      </label>
      {standing && (
        <div className="mt-2.5 space-y-1.5 border-t border-white/[0.06] pt-2.5 text-xs" data-testid="standing-state">
          <div className="flex flex-wrap items-center gap-2">
            {standing.active ? <Badge tone="good">Dauerfreigabe gilt</Badge> : <Badge tone="warn">gilt nicht mehr</Badge>}
            <span className="text-white/60">
              erteilt von {standing.granted_by_label} am {formatDate(standing.granted_at)} für{" "}
              {standing.hosts.map(describeStandingHost).join(", ") || "–"}
            </span>
            {canGrant && (
              <Button small variant="ghost" disabled={busy} onClick={onRevoke}>Freigabe zurückziehen</Button>
            )}
          </div>
          {!standing.active && standing.problem && (
            <p className="text-amber-300">
              Gilt nicht mehr: {standing.problem} Geplante Läufe warten auf deine Freigabe, beim nächsten Lauf erlischt
              sie.
            </p>
          )}
          {standing.active && (standing.new_hosts?.length ?? 0) > 0 && (
            <p className="text-white/55">
              Neu dabei: {standing.new_hosts!.join(", ")}. Dort fragt das Dashboard weiter nach, bis du neu freigibst.
            </p>
          )}
          {standing.active && dirty && (
            <p className="text-amber-300">Wenn du deine Änderungen speicherst, erlischt die Dauerfreigabe.</p>
          )}
        </div>
      )}
      {!standing && !dirty && (script.standing_preview?.length ?? 0) > 0 && (
        <p className="mt-2 text-xs text-white/55" data-testid="standing-preview">
          Würde gelten für: {script.standing_preview!.map(describeStandingHost).join(", ")}
        </p>
      )}
      {!standing && !canGrant && <p className="mt-2 text-xs text-white/45">Einschalten können nur Owner oder Admin.</p>}
      {!standing && canGrant && dirty && <p className="mt-2 text-xs text-white/45">Speichere zuerst deine Änderungen.</p>}
      {!standing && canGrant && !dirty && !script.enabled && (
        <p className="mt-2 text-xs text-white/45">Das Skript ist aus – schalte es zuerst auf „aktiv“ und speichere.</p>
      )}
    </div>
  );
}

/** Wie Parameter im Skript stehen und was mit `$` passiert (params.py im Backend): Ersetzt
 * werden nur deklarierte Parameter, `$$` wird zu einem `$`, alles andere bleibt. */
export function ParamsHint({ names }: { names: string[] }): JSX.Element {
  const code = "rounded bg-white/[0.06] px-1 font-mono text-[11px] text-white/75";
  return (
    <p className="mt-2 flex items-start gap-1.5 text-xs text-white/55" data-testid="params-hint">
      <Icon name="code" size={12} className="mt-0.5 flex-none" />
      <span>
        {names.length > 0 && (
          <>
            Parameter dieses Skripts:{" "}
            {names.map((n, i) => (
              <span key={n}>{i > 0 && ", "}<code className={code}>{`$${n}`}</code></span>
            ))}
            .{" "}
          </>
        )}
        Parameter schreibst du als <code className={code}>$name</code> oder <code className={code}>{"${name}"}</code>,
        ohne Anführungszeichen drumherum – das Dashboard setzt den Wert schon sicher ein. Alles andere mit{" "}
        <code className={code}>$</code> (z. B.{" "}
        <code className={code}>$HOME</code>, <code className={code}>{'"$f"'}</code>, <code className={code}>$(date)</code>)
        bleibt, wie es ist. Nur <code className={code}>$$</code> wird zu einem einzelnen <code className={code}>$</code>:
        Für ein <code className={code}>$</code> direkt vor einem Parameternamen schreibst du{" "}
        <code className={code}>$$</code>, für die Prozessnummer <code className={code}>$$</code> schreibst du{" "}
        <code className={code}>$$$$</code>.
      </span>
    </p>
  );
}

function targetLabel(target: ScriptOut["target"], hosts: HostRow[], groups: { id: string; name: string }[]): string {
  if (target.kind === "all") return "Alle Server";
  if (target.kind === "group") return groups.find((g) => g.id === target.group_id)?.name ?? "Gruppe";
  const host = hosts.find((h) => h.id === target.host_id);
  return host ? host.display_name || host.name : "Ein Server";
}

export function ScriptsPage(): JSX.Element {
  // Sprung von der Server-Seite des Kerns (`?host=`): Skripte dieses Hosts, und ein
  // neues Skript zielt gleich auf ihn. Der Filter folgt der Adresszeile (location.ts): ein
  // Link auf die schon offene Seite setzt ihn wieder, auch nachdem man ihn hier entfernt hat.
  const [urlParams, updateUrl] = useUrlParams();
  const hostFilter = urlParams.get("host") || null;
  const [scripts, setScripts] = useState<ScriptOut[] | null>(null);
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const [hosts, setHosts] = useState<HostRow[]>([]);
  const [groups, setGroups] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ScriptOut>(() => emptyDraft(hostFilter));
  const [isNew, setIsNew] = useState(true);
  const [idTouched, setIdTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  // Zaehlt die Laeufe hoch: ein spaetes Ergebnis eines laengst abgeloesten Laufs (oder
  // nach Verlassen der Seite) ueberschreibt keine neuere Meldung.
  const runSeq = useRef(0);
  useEffect(() => () => { runSeq.current += 1; }, []);
  const unmountSignal = useUnmountSignal();
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [runs, setRuns] = useState<RunOut[]>([]);
  const [executions, setExecutions] = useState<ExecutionOut[]>([]);
  const [history, setHistory] = useState<{ sha: string; message: string; commit_time: number }[]>([]);

  const loadExecutions = useCallback((scriptId: string) => {
    authedFetch(`/ext/scripts/scripts/${scriptId}/runs`)
      .then((res) => (res.ok ? (res.json() as Promise<ExecutionOut[]>) : []))
      .then(setExecutions)
      .catch(() => setExecutions([]));
  }, []);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      authedFetch("/ext/scripts/scripts").then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ScriptOut[]>;
      }),
      authedFetch("/jobs?ext_id=scripts").then((res) => (res.ok ? (res.json() as Promise<JobOut[]>) : [])),
    ])
      .then(([s, j]) => {
        setScripts(s);
        setJobs(j);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
    authedFetch("/hosts").then((r) => (r.ok ? (r.json() as Promise<HostRow[]>) : [])).then(setHosts).catch(() => setHosts([]));
    authedFetch("/host-groups").then((r) => (r.ok ? r.json() : [])).then(setGroups).catch(() => setGroups([]));
  }, [load]);

  function selectScript(script: ScriptOut) {
    setSelectedId(script.id);
    setDraft(script);
    setIsNew(false);
    setMessage(null);
    runSeq.current += 1;
    const job = jobs.find((j) => j.ext_job_key === script.job_id);
    if (job) {
      authedFetch(`/jobs/${job.id}/runs?limit=10`)
        .then((res) => (res.ok ? (res.json() as Promise<RunOut[]>) : []))
        .then(setRuns)
        .catch(() => setRuns([]));
    } else {
      setRuns([]);
    }
    loadExecutions(script.id);
    authedFetch(`/ext/scripts/scripts/${script.id}/history`)
      .then((res) => (res.ok ? res.json() : []))
      .then(setHistory)
      .catch(() => setHistory([]));
  }

  function selectNew() {
    setSelectedId(null);
    setDraft(emptyDraft(hostFilter));
    setIsNew(true);
    setIdTouched(false);
    setMessage(null);
    runSeq.current += 1;
    setRuns([]);
    setExecutions([]);
    setHistory([]);
  }

  async function save() {
    if (!draft.id.trim()) {
      setMessage({ kind: "error", text: "Bitte einen Namen bzw. eine Kennung vergeben." });
      return;
    }
    // Ein neues Skript darf kein bestehendes still ueberschreiben. Vorab
    // pruefen, und ?create=true laesst das Backend zusaetzlich mit 409 ablehnen.
    if (isNew && scripts?.some((s) => s.id === draft.id)) {
      setMessage({ kind: "error", text: `Ein Skript mit der Kennung „${draft.id}“ gibt es schon – bitte eine andere Kennung wählen.` });
      return;
    }
    setBusy(true);
    setMessage(null);
    runSeq.current += 1;
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${draft.id}${isNew ? "?create=true" : ""}`, {
        method: "PUT",
        body: JSON.stringify({
          name: draft.name || draft.id,
          description: draft.description,
          content: draft.content,
          params_schema: draft.params_schema,
          target: draft.target,
          schedule: draft.schedule || null,
          enabled: draft.enabled,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      // Speichern kann die Dauerfreigabe aufheben -- den neuen Stand gleich anzeigen.
      setDraft((prev) => ({
        ...prev,
        fingerprint: body.fingerprint,
        standing_approval: body.standing_approval ?? null,
        standing_preview: body.standing_preview ?? null,
        targets_fingerprint: body.targets_fingerprint ?? null,
      }));
      setMessage({ kind: "ok", text: "Gespeichert." });
      setIsNew(false);
      setSelectedId(draft.id);
      load();
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selectedId) return;
    const ok = await deck().confirmDialog(`Skript „${draft.name || selectedId}“ löschen?`, { danger: true, confirmLabel: "Löschen" });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${selectedId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      selectNew();
      load();
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  /** Nach „Die Zielserver haben sich geändert“: die aktuelle Liste holen, damit der nächste
   * Klick sie zeigt (der Inhalt im Editor bleibt, wie er ist). */
  async function refreshPreview(scriptId: string) {
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${scriptId}`);
      if (!res.ok) return;
      const fresh = (await res.json()) as ScriptOut;
      setDraft((prev) =>
        prev.id === scriptId
          ? { ...prev, standing_preview: fresh.standing_preview ?? null, targets_fingerprint: fresh.targets_fingerprint ?? null }
          : prev,
      );
    } catch {
      // Dann bleibt es beim Hinweis, die Seite neu zu laden.
    }
  }

  async function grantStanding() {
    if (!selectedId) return;
    const servers = (draft.standing_preview ?? []).map(describeStandingHost).join(", ") || "–";
    const ok = await deck().confirmDialog(
      `„${draft.name || selectedId}“ ab jetzt nach Zeitplan ohne Freigabe laufen lassen (${targetLabel(draft.target, hosts, groups)})? ` +
        `Gilt für: ${servers}. Neue Server fragen weiter nach, und jede Änderung am Skript, an einem Konto oder ` +
        "einer Adresse hebt die Freigabe wieder auf.",
      { danger: true, confirmLabel: "Ohne Freigabe laufen lassen" },
    );
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${selectedId}/standing-approval`, {
        method: "POST",
        body: JSON.stringify({ fingerprint: draft.fingerprint ?? "", targets_fingerprint: draft.targets_fingerprint ?? "" }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 409) void refreshPreview(selectedId);
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setDraft((prev) => ({ ...prev, standing_approval: body.standing_approval ?? null }));
      setMessage({ kind: "ok", text: "Dauerfreigabe erteilt – die geplanten Läufe brauchen keinen Klick mehr." });
      load();
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  async function revokeStanding() {
    if (!selectedId) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${selectedId}/standing-approval`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      setDraft((prev) => ({ ...prev, standing_approval: null }));
      setMessage({ kind: "ok", text: "Freigabe zurückgezogen – geplante Läufe warten wieder auf deine Freigabe." });
      load();
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  async function runNow() {
    if (!selectedId) return;
    const ok = await deck().confirmDialog(`Skript „${draft.name || selectedId}“ jetzt ausführen?`, { danger: true, confirmLabel: "Ausführen" });
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    const seq = ++runSeq.current;
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${selectedId}/run`, {
        method: "POST",
        body: JSON.stringify({ param_overrides: {} }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      // Wie auf der Backups-Seite: wer freigeben darf, bestaetigt gleich mit -- der
      // Klick auf "Ausführen" samt Rueckfrage IST die Bestaetigung.
      const results: RunResult[] = body.results ?? [];
      const outcome: RunOutcome = { succeeded: 0, running: 0, waiting: 0, failed: [] };
      const canApprove = deck().hasPermission("actions.approve:high");
      // Mehrere Freigaben nacheinander duerfen nicht jeweils bis zu 20 s warten.
      const immediate = results.filter((r) => !r.skipped && !r.error && r.action_id && r.status === "proposed").length > 1;
      // Laeufe, die nach der Freigabe noch weiterlaufen: die Seite fragt danach im
      // Hintergrund nach, statt den Knopf bis zum Ende zu sperren.
      const running: { host: string; actionId: string }[] = [];
      const record = (host: string, actionId: string, verdict: RunVerdict) => {
        countOutcome(outcome, host, verdict);
        if (isActionRunning(verdict.status)) running.push({ host, actionId });
      };
      for (const r of results) {
        if (r.skipped) continue;
        const host = r.host_name ?? r.host_id ?? "?";
        if (r.error) {
          outcome.failed.push(`${host} (${r.error})`);
        } else if (r.action_id && r.status === "proposed" && canApprove) {
          // Nicht jede Freigabe zaehlt als "ausgeführt" -- eine abgelaufene (409) oder
          // ein Lauf, der auf dem Server gescheitert ist, wird als solcher gemeldet.
          record(host, r.action_id, await approveRun(r.action_id, immediate));
        } else if (r.status === "proposed") {
          outcome.waiting += 1;
        } else if (r.action_id) {
          // Autonomie „voll“: das Gate hat den Lauf schon selbst entschieden.
          record(host, r.action_id, { status: r.status ?? "" });
        }
      }
      // Bei „Allen Servern“/„Gruppe“ laesst das Backend Server ohne SSH-Zugang,
      // Windows und nicht verwaltete aus -- mit Grund, damit niemand danach sucht.
      const skipped = results.filter((r) => r.skipped).map((r) => `${r.host_name ?? "?"} (${r.skipped})`);
      setMessage({ kind: outcome.failed.length ? "error" : "ok", text: describeRun(outcome, skipped) });
      loadExecutions(selectedId);
      if (running.length > 0) {
        const scriptId = selectedId;
        // Jedes Ziel meldet sich einzeln, sobald es fertig ist -- das schnelle wartet
        // nicht auf das langsamste. Eine spaetere Meldung (Speichern, anderes Skript,
        // neuer Lauf, Seite verlassen) zaehlt runSeq hoch; dann bleibt die Anzeige stehen.
        const signal = unmountSignal();
        for (const r of running) {
          void finishRun(r.actionId, signal).then((verdict) => {
            if (runSeq.current !== seq || isActionRunning(verdict.status)) return;
            outcome.running -= 1;
            countOutcome(outcome, r.host, verdict);
            setMessage({ kind: outcome.failed.length ? "error" : "ok", text: describeRun(outcome, skipped) });
            loadExecutions(scriptId);
          });
        }
      }
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  const visibleScripts = hostFilter
    ? scripts?.filter((s) => s.target.kind === "host" && s.target.host_id === hostFilter)
    : scripts;
  const lines = draft.content.split("\n").length;
  const savedScript = scripts?.find((s) => s.id === selectedId) ?? null;
  const dirty = savedScript ? approvalRelevant(savedScript) !== approvalRelevant(draft) : true;
  const missingTarget = targetHint(draft.target);
  const showEditor = !isNew || selectedId !== null || (scripts?.length ?? 0) > 0 || draft.id !== "" || draft.name !== "";

  return (
    <Page
      title="Skripte"
      description="Eigene Befehle zentral pflegen und auf einem, mehreren oder allen Servern ausführen – sofort oder nach Zeitplan."
      actions={<Button variant="primary" onClick={selectNew}><Icon name="plus" size={14} /> Neues Skript</Button>}
    >
      {error && <Notice text={`Fehler: ${error}`} />}
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        <aside className="lg:w-72 lg:flex-none">
          <Card title={`Skripte${scripts ? ` (${visibleScripts?.length ?? 0})` : ""}`} padded={false}>
            {hostFilter && (
              <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2 text-xs" data-testid="host-filter">
                <span className="text-white/60">Nur Skripte für diesen Server</span>
                <button type="button" onClick={() => updateUrl({ host: null })} aria-label="Filter entfernen" className="text-white/40 hover:text-white">
                  <Icon name="x" size={13} />
                </button>
              </div>
            )}
            {!scripts && !error && <p className="px-4 py-3 text-sm text-white/45">Lade …</p>}
            {scripts && visibleScripts?.length === 0 && (
              <p className="px-4 py-4 text-sm text-white/45">
                {hostFilter ? "Noch kein Skript für diesen Server – „Neues Skript“ legt eins an." : "Noch keine Skripte."}
              </p>
            )}
            <ul className="divide-y divide-white/[0.05]">
              {visibleScripts?.map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => selectScript(s)}
                    className={`flex w-full items-start gap-2.5 px-4 py-3 text-left transition ${
                      selectedId === s.id ? "bg-white/[0.07]" : "hover:bg-white/[0.03]"
                    }`}
                  >
                    <Icon name="code" size={15} className={`mt-0.5 ${selectedId === s.id ? "text-[var(--color-accent)]" : "text-white/40"}`} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{s.name}</span>
                      <span className="mt-0.5 block text-xs text-white/45">
                        <Icon name="clock" size={11} className="mr-1 inline -mt-px align-middle" />
                        {describeSchedule(s.schedule)} · {targetLabel(s.target, hosts, groups)}
                      </span>
                    </span>
                    {s.standing_approval?.active && <Badge tone="info">ohne Klick</Badge>}
                    {!s.enabled && <Badge>aus</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </aside>

        <div className="min-w-0 flex-1 space-y-5">
          {!showEditor ? (
            <EmptyState
              icon="code"
              title="Noch keine Skripte"
              text="Lege z. B. ein Update-, Aufräum- oder Prüfskript an und starte es per Klick auf einem oder allen Servern – oder plane es: Dann erscheint jeder Lauf zur Freigabe unter „Aktionen“."
              action={<Button variant="primary" onClick={() => setDraft({ ...emptyDraft(hostFilter), name: "Neues Skript", id: "neues-skript" })}><Icon name="plus" size={14} /> Erstes Skript anlegen</Button>}
            />
          ) : (
            <>
              {message && <Notice text={message.text} kind={message.kind} onClose={() => setMessage(null)} />}
              <Card
                title={isNew ? "Neues Skript" : draft.name || draft.id}
                actions={
                  <label className="flex items-center gap-2 text-xs text-white/60">
                    <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
                    aktiv
                  </label>
                }
              >
                <div className="grid gap-4 md:grid-cols-2">
                  <Field label="Name">
                    <input
                      className={inputClass}
                      placeholder="z. B. Sicherheitsupdates"
                      value={draft.name}
                      onChange={(e) => setDraft({ ...draft, name: e.target.value, id: isNew && !idTouched ? slugify(e.target.value) : draft.id })}
                    />
                  </Field>
                  <Field label="Kennung">
                    <input
                      className={`${inputClass} font-mono`}
                      placeholder="skript-id"
                      value={draft.id}
                      disabled={!isNew}
                      onChange={(e) => { setIdTouched(true); setDraft({ ...draft, id: e.target.value }); }}
                    />
                  </Field>
                  <Field label="Ausführen auf">
                    <div className="flex gap-2">
                      <select
                        className={`${inputClass} w-auto`}
                        aria-label="Ziel-Art"
                        value={draft.target.kind ?? "host"}
                        onChange={(e) => setDraft({ ...draft, target: { kind: e.target.value } })}
                      >
                        <option value="host">Einem Server</option>
                        <option value="group">Einer Gruppe</option>
                        <option value="all">Allen Servern</option>
                      </select>
                      {draft.target.kind === "host" && (
                        <select
                          className={inputClass}
                          aria-label="Server"
                          value={draft.target.host_id ?? ""}
                          onChange={(e) => setDraft({ ...draft, target: { kind: "host", host_id: e.target.value || null } })}
                        >
                          <option value="">Server wählen …</option>
                          {hosts.map((h) => <option key={h.id} value={h.id}>{h.display_name || h.name}</option>)}
                          {draft.target.host_id && !hosts.some((h) => h.id === draft.target.host_id) && <option value={draft.target.host_id}>{draft.target.host_id}</option>}
                        </select>
                      )}
                      {draft.target.kind === "group" && (
                        <select
                          className={inputClass}
                          aria-label="Gruppe"
                          value={draft.target.group_id ?? ""}
                          onChange={(e) => setDraft({ ...draft, target: { kind: "group", group_id: e.target.value || null } })}
                        >
                          <option value="">Gruppe wählen …</option>
                          {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                        </select>
                      )}
                    </div>
                  </Field>
                  <div className="md:col-span-2">
                    <p className="mb-1.5 text-sm text-white/70">Zeitplan</p>
                    <SchedulePicker
                      label="Zeitplan"
                      allowOff
                      offLabel="Manuell"
                      value={draft.schedule}
                      onChange={(cron) => setDraft({ ...draft, schedule: cron || null })}
                    />
                    {/* Ein geplanter Lauf ist nur ein Vorschlag (Risiko „hoch“
                        ueber das Gate) -- ohne Hinweis sah der Zeitplan nach Automatik aus. */}
                    {draft.schedule && (
                      <p className="mt-2 flex items-start gap-1.5 text-xs text-white/55" data-testid="schedule-hint">
                        <Icon name="clock" size={12} className="mt-0.5 flex-none" />
                        <span>
                          Geplante Läufe erscheinen als Vorschlag unter „Aktionen“ und müssen dort freigegeben werden,
                          sonst verfallen sie nach 24 Stunden. Ohne Rückfrage laufen sie nur mit einer Dauerfreigabe
                          (Schalter darunter) oder wenn unter Einstellungen → Automatik „Selbstständig handeln“ bis
                          Risikostufe „Hoch“ oder „Kritisch“ erlaubt ist.
                        </span>
                      </p>
                    )}
                    {!isNew && (draft.schedule || draft.standing_approval) && (
                      <StandingApprovalPanel
                        script={draft}
                        dirty={dirty}
                        canGrant={deck().hasPermission("actions.standing_approval")}
                        busy={busy}
                        onGrant={() => void grantStanding()}
                        onRevoke={() => void revokeStanding()}
                      />
                    )}
                  </div>
                </div>

                <div className="mt-4 overflow-hidden rounded-lg border border-white/10 bg-black/40">
                  <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-1.5 text-[11px] text-white/40">
                    <span>Shell-Skript</span>
                    <span>{lines} Zeilen</span>
                  </div>
                  <textarea
                    aria-label="Skript-Inhalt"
                    className="block h-72 w-full resize-y bg-transparent p-3 font-mono text-xs leading-relaxed text-white/90 outline-none"
                    spellCheck={false}
                    value={draft.content}
                    onChange={(e) => setDraft({ ...draft, content: e.target.value })}
                  />
                </div>
                <ParamsHint names={Object.keys(draft.params_schema ?? {})} />

                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <Button variant="primary" disabled={busy || missingTarget !== null} onClick={() => void save()}>Speichern</Button>
                  {missingTarget && <span className="text-xs text-amber-300">{missingTarget}</span>}
                  {!isNew && (
                    <>
                      <Button disabled={busy} onClick={() => void runNow()}><Icon name="play" size={13} /> Jetzt ausführen</Button>
                      <span className="flex-1" />
                      <Button variant="danger" disabled={busy} onClick={() => void remove()}><Icon name="trash" size={13} /> Löschen</Button>
                    </>
                  )}
                </div>
              </Card>

              {!isNew && (
                <Card
                  title="Ausführungen"
                  description="Die letzten Läufe mit Ausgabe. Aufklappen zeigt, was das Skript ausgegeben hat."
                  actions={<Button small variant="ghost" onClick={() => selectedId && loadExecutions(selectedId)}><Icon name="refresh" size={12} /> Aktualisieren</Button>}
                >
                  <Executions items={executions} />
                </Card>
              )}

              {!isNew && (
                <div className="grid gap-5 md:grid-cols-2">
                  <Card title="Zeitplan-Läufe">
                    <ul className="space-y-1.5 text-sm">
                      {runs.length === 0 && <li className="text-white/45">Noch keine Läufe.</li>}
                      {runs.map((r) => (
                        <li key={r.id} className="flex flex-wrap items-center gap-2">
                          <Badge tone={r.status === "succeeded" ? "good" : r.status === "failed" ? "bad" : "neutral"}>
                            {r.status === "succeeded" ? "erfolgreich" : r.status === "failed" ? "fehlgeschlagen" : r.status}
                          </Badge>
                          <span className="text-white/70">{new Date(r.started_at).toLocaleString("de-DE")}</span>
                          <span className="text-xs text-white/40">{r.trigger === "schedule" ? "Zeitplan" : r.trigger}</span>
                          {r.error && <span className="w-full text-xs text-red-300">{r.error}</span>}
                        </li>
                      ))}
                    </ul>
                  </Card>
                  <Card title="Versionen" description="Jede Änderung wird gespeichert.">
                    <ul className="space-y-1.5 text-sm">
                      {history.length === 0 && <li className="text-white/45">Keine Versionen.</li>}
                      {history.map((h) => (
                        <li key={h.sha} className="flex items-baseline gap-2">
                          <code className="text-[11px] text-white/35">{h.sha.slice(0, 7)}</code>
                          <span className="min-w-0 flex-1 truncate text-white/75">{h.message}</span>
                          <span className="text-xs text-white/35">{new Date(h.commit_time * 1000).toLocaleDateString("de-DE")}</span>
                        </li>
                      ))}
                    </ul>
                  </Card>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </Page>
  );
}
