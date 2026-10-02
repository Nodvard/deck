/**
 * Aktionen-Seite: die Oberflaeche zum Propose+Confirm-Gate (docs/00-DECISIONS.md,
 * core/gate.py) ueber `GET/POST /actions*` (Liste, Details, approve/reject/dismiss).
 * Bei `autonomy.mode=propose` (Default) landet JEDE Aktion -- ob von nexus-soc
 * vorgeschlagen oder von einem Nutzer ueber eine Extension-Seite ausgeloest -- als
 * `status=proposed` in der `actions`-Tabelle und bliebe ohne diese Seite dort
 * liegen: hier wird sie bestaetigt oder abgelehnt.
 *
 * Core-Seite wie DashboardPage.tsx/FilesPage.tsx, keine Extension-Seite: die
 * `actions`-Tabelle ist kernseitig (docs/03 §1), unabhaengig davon, welche
 * Extension einen Vorschlag erzeugt hat.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { actionReason, describeActionOutcome, OUTCOME_TEXT_CLASS, OUTPUT_HIDDEN_HINT, type ActionOutcome } from "../lib/actionOutcome";
import {
  ACTION_POLL_INTERVAL_MS,
  ActionWaitTimeout,
  GAVE_UP_WAITING_TEXT,
  isAbortError,
  isActionRunning,
  RUNNING_IN_BACKGROUND_TEXT,
  waitForAction,
} from "../lib/actions";
import { api, ApiError } from "../lib/api";
import { visibleCommand } from "../lib/visibleCommand";
import { confirmDialog, promptDialog } from "../state/dialogs";
import { useAuthStore } from "../state/auth";

interface ActionOut {
  id: string;
  ext_id: string;
  action_type: string;
  host_id: string | null;
  /** Befehl (`command`) und weitere Einzelheiten; ohne Server-Rechte nur Kennungen, siehe `payload_hidden`. */
  payload: Record<string, unknown>;
  /** Befehl und Parameter fehlen, weil dem Nutzer die Server-Rechte fehlen. */
  payload_hidden?: boolean;
  risk: string;
  status: string;
  proposed_by_type: string;
  proposed_by_id: string;
  /** Name statt "user/<uuid>". Fehlt bei einem älteren Backend -> roher Wert. */
  proposed_by_label?: string;
  /** Wer entschieden hat: Benutzername, für Betrachter die nackte Kennung; null solange offen. */
  approved_by_user_id?: string | null;
  approved_by_label?: string | null;
  reason: string;
  // Ergebnis der Ausfuehrung und Gate-Entscheidung (api/v1/actions.py ActionOut) --
  // ohne diese Felder koennte die Seite keinen Fehlergrund zeigen.
  gate_decision: { rule?: string | null; detail?: string | null; user_reason?: string | null };
  result: { success?: boolean; error?: string | null };
  /** Ausgabe und Fehlertext fehlen, weil dem Nutzer die Server-Rechte fehlen. */
  output_hidden?: boolean;
  finished_at: string | null;
  created_at: string;
}

interface HostOut {
  id: string;
  display_name: string;
}

const STATUS_FILTERS = [
  { value: "proposed", label: "Wartet auf Bestätigung" },
  { value: "", label: "Alle" },
  { value: "executing", label: "Läuft" },
  { value: "succeeded", label: "Erfolgreich" },
  { value: "failed", label: "Fehlgeschlagen" },
  { value: "denied", label: "Abgelehnt" },
  { value: "dismissed", label: "Verworfen" },
  { value: "expired", label: "Abgelaufen" },
];

const STATUS_LABEL: Record<string, string> = {
  proposed: "Wartet", approved: "Genehmigt", executing: "Läuft …", succeeded: "Erfolgreich",
  failed: "Fehlgeschlagen", denied: "Abgelehnt", expired: "Abgelaufen", dismissed: "Verworfen",
};

const STATUS_CLASS: Record<string, string> = {
  proposed: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  approved: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  executing: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  succeeded: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  failed: "bg-red-500/15 text-red-300 border-red-500/40",
  denied: "bg-red-500/15 text-red-300 border-red-500/40",
  dismissed: "bg-white/10 opacity-70",
  expired: "bg-white/10 opacity-70",
};

/** Aktionen mit diesem Risiko lassen sich nicht in der Sammel-Freigabe bestätigen, nur einzeln. */
const SINGLE_APPROVAL_RISKS = ["high", "critical"];

/** Der Befehl (Shell-Befehl bzw. aufgelöster Skript-Inhalt), den die Aktion ausführen würde. */
function commandOf(a: ActionOut): string | null {
  const command = a.payload?.command;
  return typeof command === "string" && command.trim() !== "" ? command : null;
}

const COMMAND_HIDDEN_HINT = "Befehl nur für Nutzer mit Server-Rechten sichtbar";

/** „Vorgeschlagen von“: der Name, den das Backend aufgelöst hat; sonst der rohe Wert. */
function proposedByText(a: ActionOut): string {
  return a.proposed_by_label || `${a.proposed_by_type}/${a.proposed_by_id}`;
}

/** „Bestätigt von anna“ / „Abgelehnt von anna“ / „Verworfen von anna“ bzw. bei „Selbstständig
 * handeln“ (autonomy:full, keine Person) „Automatisch freigegeben“. Nichts, solange offen. Nur
 * Nutzer setzen `approved_by_user_id`: Gate-Sperren (Sperrliste, Wiederholungsbremse) und
 * Abläufe haben keinen Entscheider und zeigen deshalb auch nichts. */
function decidedByText(a: ActionOut): { text: string; title?: string } | null {
  const who = a.approved_by_label || a.approved_by_user_id;
  if (who) {
    const verb = a.status === "denied" ? "Abgelehnt" : a.status === "dismissed" ? "Verworfen" : "Bestätigt";
    return { text: `${verb} von ${who}`, title: a.approved_by_user_id ? `user/${a.approved_by_user_id}` : undefined };
  }
  if (a.gate_decision?.rule === "autonomy:full") return { text: "Automatisch freigegeben" };
  return null;
}

function errorMessage(err: unknown): ActionOutcome {
  return { tone: "error", text: `Fehler: ${err instanceof ApiError ? err.message : String(err)}` };
}

/** Sammel-Freigabe: Ergebnis je Aktion, gesammelt fuer die Zusammenfassung. */
interface BulkTally {
  /** Laeuft noch im Hintergrund (202 bzw. schon von woanders bestaetigt) -- wird nachgefragt. */
  running: ActionOut[];
  /** Schon fertig gemeldet und erfolgreich. */
  done: number;
  failed: string[];
  blocked: string[];
  /** Nicht mehr offen (abgelaufen, schon entschieden) oder unbekannt. */
  stale: number;
}

function emptyTally(): BulkTally {
  return { running: [], done: 0, failed: [], blocked: [], stale: 0 };
}

function withReasons(count: number, word: string, reasons: string[]): string {
  const distinct = [...new Set(reasons.map((r) => r.trim()).filter(Boolean))];
  return distinct.length > 0 ? `${count} ${word}: ${distinct.join("; ")}` : `${count} ${word}`;
}

/** „3 gestartet, 1 gesperrt: Grund“ -- Fehlgeschlagene und Gesperrte mit Grund. */
function summarizeTally(t: BulkTally): string {
  const parts: string[] = [];
  const started = t.running.length + t.done;
  if (started > 0) parts.push(`${started} gestartet`);
  if (t.failed.length > 0) parts.push(withReasons(t.failed.length, "fehlgeschlagen", t.failed));
  if (t.blocked.length > 0) parts.push(withReasons(t.blocked.length, "gesperrt", t.blocked));
  if (t.stale > 0) parts.push(`${t.stale} nicht mehr offen`);
  return parts.join(", ");
}

/** Endstand der im Hintergrund laufenden Aktionen: „2 ausgeführt, 1 fehlgeschlagen: Grund“. */
function summarizeFinished(finished: ActionOut[]): { text: string; failed: boolean } {
  const failed = finished.filter((a) => a.status !== "succeeded");
  const ok = finished.length - failed.length;
  const parts: string[] = [];
  if (ok > 0) parts.push(`${ok} ausgeführt`);
  if (failed.length > 0) {
    parts.push(withReasons(failed.length, "fehlgeschlagen", failed.map((a) => actionReason(a) ?? describeActionOutcome(a).text)));
  }
  return { text: parts.join(", "), failed: failed.length > 0 };
}

/** So viele spaete Fehlschlaege aelterer Freigaben stehen hoechstens da. */
const MAX_LATE_ERRORS = 5;

export function ActionsPage(): JSX.Element {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const [statusFilter, setStatusFilter] = useState("proposed");
  const [actions, setActions] = useState<ActionOut[] | null>(null);
  const [hostNames, setHostNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  // Mehrere Freigaben koennen gleichzeitig laufen (die Antwort dauert bis zu 20 s) --
  // jede Zeile ist nur solange gesperrt, wie ihre eigene Anfrage laeuft.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [message, setMessage] = useState<ActionOutcome | null>(null);
  // Fehlschlaege aelterer Freigaben, die erst nach dem Start einer neueren ankamen: sie
  // duerfen die neuere Meldung nicht ersetzen, aber auch nicht verschwinden -- im
  // Standardfilter ist die fehlgeschlagene Zeile nach dem Neuladen schon weg.
  const [lateErrors, setLateErrors] = useState<ActionOutcome[]>([]);
  // Sammel-Freigabe. `bulkRunning` (Ref) sperrt einen zweiten Klick sofort,
  // noch bevor React `bulkBusy` neu gezeichnet hat.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const bulkRunning = useRef(false);
  // Zaehlt jede neue Meldungs-Runde: ein spaet eintreffendes Ergebnis einer aelteren
  // Freigabe darf die neuere Meldung nicht ueberschreiben.
  const messageSeq = useRef(0);
  // Laufende Nachfragen zu Aktionen im Hintergrund enden mit der Seite.
  const pollAbort = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    setError(null);
    const query = statusFilter ? `?status_=${encodeURIComponent(statusFilter)}` : "";
    api
      .get<ActionOut[]>(`/actions${query}`)
      .then(setActions)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)));
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const controller = new AbortController();
    pollAbort.current = controller;
    return () => controller.abort();
  }, []);

  // Solange eine Zeile noch läuft (auch aus einem anderen Tab gestartet),
  // die Liste regelmäßig neu laden, damit „Läuft …“ von selbst zum Ergebnis wird.
  const hasRunning = (actions ?? []).some((a) => isActionRunning(a.status));
  useEffect(() => {
    if (!hasRunning) return;
    const timer = setInterval(load, ACTION_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasRunning, load]);

  useEffect(() => {
    api
      .get<HostOut[]>("/hosts")
      .then((hosts) => setHostNames(Object.fromEntries(hosts.map((h) => [h.id, h.display_name]))))
      .catch(() => {
        // Host-Namen sind nur eine Anzeige-Annehmlichkeit -- schlaegt das fehl, zeigt
        // die Tabelle einfach die rohe host_id weiter (siehe hostLabel() unten).
      });
  }, []);

  // Auswahl nur behalten, solange die Zeile noch auf Freigabe wartet.
  useEffect(() => {
    if (!actions) return;
    const open = new Set(actions.filter((a) => a.status === "proposed").map((a) => a.id));
    setSelected((prev) => {
      const kept = [...prev].filter((id) => open.has(id));
      return kept.length === prev.size ? prev : new Set(kept);
    });
  }, [actions]);

  const selectable = (actions ?? []).filter((a) => a.status === "proposed" && hasPermission(`actions.approve:${a.risk}`));
  const hasSingleOnly = selectable.some((a) => SINGLE_APPROVAL_RISKS.includes(a.risk));
  const selectedActions = selectable.filter((a) => selected.has(a.id));
  // Hohe und kritische Risiken gibt es nur einzeln: so steht der Befehl vor jeder Freigabe vor Augen.
  const approvable = selectedActions.filter((a) => !SINGLE_APPROVAL_RISKS.includes(a.risk));
  const allSelected = selectable.length > 0 && selectedActions.length === selectable.length;
  const anyBusy = busyIds.size > 0 || bulkBusy;

  function markBusy(id: string, busy: boolean) {
    setBusyIds((prev) => {
      if (prev.has(id) === busy) return prev;
      const next = new Set(prev);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(selectable.map((a) => a.id)));
  }

  function hostLabel(hostId: string | null): string {
    if (!hostId) return "–";
    return hostNames[hostId] ?? hostId;
  }

  /** Beginnt eine neue Meldungs-Runde und liefert ihre Nummer fuer `showMessage`. */
  function clearMessage(): number {
    messageSeq.current += 1;
    setMessage(null);
    setLateErrors([]);
    return messageSeq.current;
  }

  /** Zeigt die Meldung nur, wenn seit `seq` keine neuere Runde begonnen hat -- die
   * Antwort einer aelteren Freigabe kommt oft erst nach der einer neueren an. Ein
   * Fehlschlag einer aelteren Runde wird trotzdem gezeigt (zusaetzlich, mit dem Namen der
   * Aktion); nur Erfolge und Zwischenstaende einer aelteren Runde fallen still weg. */
  function showMessage(seq: number, next: ActionOutcome, action?: ActionOut) {
    if (seq === messageSeq.current) {
      setMessage(next);
      return;
    }
    if (next.tone !== "error") return;
    const text = action && !next.text.startsWith(`${action.action_type}: `) ? `${action.action_type}: ${next.text}` : next.text;
    setLateErrors((prev) => (prev.some((e) => e.text === text) ? prev : [...prev, { tone: "error" as const, text }].slice(-MAX_LATE_ERRORS)));
  }

  /** Die Befehle der Aktionen, damit die Rueckfrage zeigt, was wirklich laeuft. */
  function describeCommands(batch: ActionOut[]): string {
    const lines = batch.flatMap((a) => {
      const command = commandOf(a);
      if (command === null) return [];
      // Ungekürzt: wer freigibt, soll genau den Befehl lesen, der läuft (der Dialog scrollt).
      return [`${a.action_type} auf ${hostLabel(a.host_id)}:\n${visibleCommand(command)}`];
    });
    return lines.length > 0 ? `\n\nBefehle:\n${lines.join("\n\n")}` : "";
  }

  /** Kurze Aufzaehlung fuer die Rueckfrage: Typ x Anzahl und betroffene Server. */
  function describeBatch(batch: ActionOut[]): string {
    const types = new Map<string, number>();
    const hosts = new Set<string>();
    for (const a of batch) {
      const label = `${a.ext_id}/${a.action_type}`;
      types.set(label, (types.get(label) ?? 0) + 1);
      if (a.host_id) hosts.add(hostLabel(a.host_id));
    }
    const typeText = [...types].map(([type, n]) => `${type} ×${n}`).join(", ");
    const names = [...hosts];
    const hostText = names.length > 5 ? `${names.slice(0, 5).join(", ")} und ${names.length - 5} weitere` : names.join(", ");
    return hostText ? `${typeText}; Server: ${hostText}` : typeText;
  }

  function report(seq: number, action: ActionOut, res: ActionOut) {
    const outcome = describeActionOutcome(res);
    showMessage(seq, { tone: outcome.tone, text: `${action.action_type}: ${outcome.text}` }, action);
  }

  /** Die Aktion laeuft noch im Hintergrund -- nachfragen, bis sie fertig
   * ist, dann das Ergebnis melden. Die Knoepfe sind in der Zeit schon wieder frei. */
  async function follow(seq: number, action: ActionOut) {
    try {
      const res = await waitForAction<ActionOut>(action.id, { signal: pollAbort.current?.signal });
      report(seq, action, res);
    } catch (err) {
      if (isAbortError(err)) return;
      if (err instanceof ActionWaitTimeout) showMessage(seq, { tone: "pending", text: `${action.action_type}: ${GAVE_UP_WAITING_TEXT}` });
      else showMessage(seq, errorMessage(err), action);
    }
    load();
  }

  async function approve(action: ActionOut) {
    markBusy(action.id, true);
    const seq = clearMessage();
    try {
      // approve liefert das Ergebnis mit -- "Bestätigt" allein hiesse auch bei einem
      // Fehlschlag, dass alles geklappt hat. Ist die Aktion nach ~20 s nicht fertig,
      // kommt 202 mit 'executing'.
      const res = await api.post<ActionOut>(`/actions/${action.id}/approve`);
      if (isActionRunning(res.status)) {
        showMessage(seq, { tone: "pending", text: `${action.action_type}: ${RUNNING_IN_BACKGROUND_TEXT}` });
        void follow(seq, action);
      } else {
        report(seq, action, res);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && /jetzt: '(approved|executing)'/.test(err.message)) {
        // Schon anderswo bestaetigt (zweiter Klick, anderer Tab) und laeuft noch --
        // kein Fehler, sondern dasselbe wie ein 202: nachfragen bis zum Ergebnis.
        showMessage(seq, { tone: "pending", text: `${action.action_type}: Wurde schon bestätigt. ${RUNNING_IN_BACKGROUND_TEXT}` });
        void follow(seq, action);
      } else {
        showMessage(seq, errorMessage(err), action);
      }
    } finally {
      // Auch nach einem Fehler neu laden: bei 409 (inzwischen abgelaufen/entschieden)
      // blieb sonst die veraltete Zeile mit aktiven Knoepfen stehen.
      load();
      markBusy(action.id, false);
    }
  }

  async function reject(action: ActionOut) {
    const reason = await promptDialog(`Ablehnen von "${action.action_type}" – Begründung:`);
    if (!reason) return;
    markBusy(action.id, true);
    const seq = clearMessage();
    try {
      await api.post(`/actions/${action.id}/reject`, { reason });
      showMessage(seq, { tone: "neutral", text: `Abgelehnt: ${action.action_type}.` }, action);
    } catch (err) {
      showMessage(seq, errorMessage(err), action);
    } finally {
      load();
      markBusy(action.id, false);
    }
  }

  async function dismiss(action: ActionOut) {
    const ok = await confirmDialog(`"${action.action_type}" verwerfen, ohne sie auszuführen?`);
    if (!ok) return;
    markBusy(action.id, true);
    const seq = clearMessage();
    try {
      await api.post(`/actions/${action.id}/dismiss`);
      showMessage(seq, { tone: "neutral", text: `Verworfen: ${action.action_type}.` });
    } catch (err) {
      showMessage(seq, errorMessage(err));
    } finally {
      load();
      markBusy(action.id, false);
    }
  }

  /** Jede ausgewaehlte Aktion einzeln freigeben -- jede geht durch das Gate
   * und ihre eigene Rechtepruefung. `?wait=0`: die API wartet nicht auf das Ergebnis,
   * sonst haengt der Sammelklick bis zu 20 s pro Aktion. */
  async function approveSelected() {
    if (bulkRunning.current || approvable.length === 0) return;
    const batch = approvable;
    bulkRunning.current = true;
    setBulkBusy(true);
    try {
      const ok = await confirmDialog(
        `${batch.length} ausgewählte ${batch.length === 1 ? "Aktion" : "Aktionen"} bestätigen und jetzt ausführen? (${describeBatch(batch)})${describeCommands(batch)}`,
        { confirmLabel: "Freigeben" },
      );
      if (!ok) return;
      clearMessage();
      const tally = emptyTally();
      for (const action of batch) {
        try {
          const res = await api.post<ActionOut>(`/actions/${encodeURIComponent(action.id)}/approve?wait=0`);
          if (isActionRunning(res.status)) tally.running.push(res);
          else if (res.status === "succeeded") tally.done += 1;
          else if (res.status === "denied") tally.blocked.push(actionReason(res) ?? "von einer Sicherheitsregel gesperrt");
          else tally.failed.push(actionReason(res) ?? describeActionOutcome(res).text);
        } catch (err) {
          if (err instanceof ApiError && err.status === 409 && /jetzt: '(approved|executing)'/.test(err.message)) {
            tally.running.push(action); // schon anderswo bestaetigt -- wie ein 202
          } else if (err instanceof ApiError && err.status === 409) {
            tally.stale += 1;
          } else if (err instanceof ApiError && err.status === 403) {
            tally.blocked.push(`${action.ext_id}/${action.action_type}: ${err.message}`);
          } else {
            tally.failed.push(err instanceof ApiError ? err.message : String(err));
          }
        }
      }
      const head = summarizeTally(tally);
      const problem = tally.failed.length + tally.blocked.length + tally.stale > 0;
      if (tally.running.length > 0) {
        setMessage({ tone: "pending", text: `${head}. ${RUNNING_IN_BACKGROUND_TEXT}` });
        void followBatch(tally.running, head);
      } else {
        setMessage({ tone: problem ? "error" : "success", text: head });
      }
      setSelected(new Set());
    } finally {
      load();
      bulkRunning.current = false;
      setBulkBusy(false);
    }
  }

  /** Wie `follow()`, nur fuer mehrere: erst wenn alle fertig sind, steht das Ergebnis da. */
  async function followBatch(running: ActionOut[], head: string) {
    const signal = pollAbort.current?.signal;
    const seq = messageSeq.current;
    const settled = await Promise.allSettled(running.map((a) => waitForAction<ActionOut>(a.id, { signal })));
    if (signal?.aborted) return;
    load();
    const finished: ActionOut[] = [];
    let gaveUp = 0;
    for (const r of settled) {
      if (r.status === "fulfilled") finished.push(r.value);
      else gaveUp += 1;
    }
    const { text, failed } = summarizeFinished(finished);
    const tail = [text, gaveUp > 0 ? `${gaveUp} laufen noch – ${GAVE_UP_WAITING_TEXT}` : ""].filter(Boolean).join(", ");
    // Eine neuere Meldung gibt es schon: ein Erfolg faellt still weg, ein Fehlschlag kommt dazu.
    showMessage(seq, { tone: failed || gaveUp > 0 ? "error" : "success", text: `${head}. Ergebnis: ${tail}` });
  }

  async function dismissSelected() {
    if (bulkRunning.current || selectedActions.length === 0) return;
    const batch = selectedActions;
    bulkRunning.current = true;
    setBulkBusy(true);
    try {
      const ok = await confirmDialog(
        `${batch.length} ausgewählte ${batch.length === 1 ? "Aktion" : "Aktionen"} verwerfen, ohne sie auszuführen? (${describeBatch(batch)})`,
      );
      if (!ok) return;
      clearMessage();
      let dismissed = 0;
      let stale = 0;
      const failed: string[] = [];
      for (const action of batch) {
        try {
          await api.post(`/actions/${encodeURIComponent(action.id)}/dismiss`);
          dismissed += 1;
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) stale += 1;
          else failed.push(err instanceof ApiError ? err.message : String(err));
        }
      }
      const parts: string[] = [];
      if (dismissed > 0) parts.push(`${dismissed} verworfen`);
      if (failed.length > 0) parts.push(withReasons(failed.length, "fehlgeschlagen", failed));
      if (stale > 0) parts.push(`${stale} nicht mehr offen`);
      setMessage({ tone: failed.length + stale > 0 ? "error" : "neutral", text: parts.join(", ") });
      setSelected(new Set());
    } finally {
      load();
      bulkRunning.current = false;
      setBulkBusy(false);
    }
  }

  return (
    <div className="p-4 sm:p-6">
      <h2 className="mb-1 text-lg font-semibold">Aktionen</h2>
      <p className="mb-4 text-sm opacity-70">
        Vorschläge aus Modulen (z. B. Updates, Docker-Dienste, Skripte) und Nodvard KI (Container-Wache in Nodvard Shield) --
        jede Aktion läuft über dieselbe Bestätigung, egal wer sie vorgeschlagen hat.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="text-sm">
          Status:{" "}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded bg-white/10 px-2 py-1 text-sm"
          >
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Sammel-Freigabe -- nur fuer Zeilen, die der Nutzer entscheiden darf. */}
      {selectable.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded border border-white/10 bg-white/5 px-3 py-2 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={allSelected}
              disabled={anyBusy}
              onChange={toggleAll}
              className="h-4 w-4"
            />
            Alle auswählen
          </label>
          <button
            type="button"
            disabled={anyBusy || approvable.length === 0}
            onClick={() => void approveSelected()}
            className="rounded bg-emerald-500/20 px-3 py-1.5 text-xs hover:bg-emerald-500/30 disabled:opacity-40"
          >
            Ausgewählte freigeben ({approvable.length})
          </button>
          <button
            type="button"
            disabled={anyBusy || selectedActions.length === 0}
            onClick={() => void dismissSelected()}
            className="rounded bg-white/10 px-3 py-1.5 text-xs hover:bg-white/20 disabled:opacity-40"
          >
            Ausgewählte verwerfen ({selectedActions.length})
          </button>
        </div>
      )}

      {hasSingleOnly && (
        <p className="mb-3 text-xs opacity-70">
          Aktionen mit hohem oder kritischem Risiko gibt „Ausgewählte freigeben“ nicht mit frei. Bestätige sie einzeln – so siehst du jeden Befehl, bevor er läuft.
        </p>
      )}

      {error && <p className="text-sm text-red-400">Fehler: {error}</p>}
      {message && <p className={`mb-2 text-sm ${OUTCOME_TEXT_CLASS[message.tone]}`}>{message.text}</p>}
      {lateErrors.map((late) => (
        <p key={late.text} className={`mb-2 text-sm ${OUTCOME_TEXT_CLASS.error}`}>{late.text}</p>
      ))}
      {!actions && !error && <p className="text-sm opacity-60">Lade …</p>}
      {actions && actions.length === 0 && <p className="text-sm opacity-60">Keine Aktionen in diesem Filter.</p>}

      {/* Handy: die Tabelle scrollt in sich, die Seite bleibt schmal. */}
      {actions && actions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-xs uppercase opacity-60">
                <th className="w-8 py-1">
                  <span className="sr-only">Auswahl</span>
                </th>
                <th className="py-1">Zeit</th>
                <th className="py-1">Aktion</th>
                <th className="py-1">Server</th>
                <th className="py-1">Risiko</th>
                <th className="py-1">Status</th>
                <th className="py-1">Vorgeschlagen von</th>
                <th className="py-1">Begründung</th>
                <th className="py-1">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {actions.map((a) => {
                const canDecide = hasPermission(`actions.approve:${a.risk}`);
                const why = actionReason(a);
                const decided = a.status === "proposed" ? null : decidedByText(a);
                return (
                  <tr key={a.id}>
                    <td className="py-1.5 pr-2">
                      {a.status === "proposed" && canDecide && (
                        <input
                          type="checkbox"
                          checked={selected.has(a.id)}
                          disabled={anyBusy}
                          onChange={() => toggleSelected(a.id)}
                          aria-label={`Auswählen: ${a.ext_id}/${a.action_type} auf ${hostLabel(a.host_id)}`}
                          className="h-4 w-4"
                        />
                      )}
                    </td>
                    <td className="py-1.5 opacity-70">{new Date(a.created_at).toLocaleString()}</td>
                    <td className="py-1.5">
                      {a.ext_id}/{a.action_type}
                      {commandOf(a) !== null ? (
                        <pre
                          data-testid={`command-${a.id}`}
                          className="mt-1 max-h-40 max-w-md overflow-auto whitespace-pre-wrap break-all rounded bg-black/30 p-1.5 font-mono text-xs"
                        >
                          {visibleCommand(commandOf(a) ?? "")}
                        </pre>
                      ) : (
                        a.payload_hidden && <p className="mt-1 max-w-xs break-words text-xs opacity-70">{COMMAND_HIDDEN_HINT}</p>
                      )}
                    </td>
                    <td className="py-1.5">{hostLabel(a.host_id)}</td>
                    <td className="py-1.5 opacity-70">{a.risk}</td>
                    <td className="py-1.5">
                      <span className={`rounded border px-1.5 py-0.5 text-xs ${STATUS_CLASS[a.status] ?? "bg-white/10 opacity-70"}`}>
                        {STATUS_LABEL[a.status] ?? a.status}
                      </span>
                      {why && (
                        <p className={`mt-1 max-w-xs break-words text-xs ${describeActionOutcome(a).tone === "error" ? "text-red-300" : "opacity-70"}`}>
                          {why}
                        </p>
                      )}
                      {a.output_hidden && (
                        <p className="mt-1 max-w-xs break-words text-xs opacity-70">{OUTPUT_HIDDEN_HINT}</p>
                      )}
                      {decided && (
                        <p className="mt-1 max-w-xs break-words text-xs opacity-70" title={decided.title}>
                          {decided.text}
                        </p>
                      )}
                    </td>
                    <td className="py-1.5 opacity-70">
                      {/* Die rohe Kennung bleibt als Tooltip erreichbar (z. B. zum Nachschlagen im Protokoll). */}
                      <span className="break-words" title={`${a.proposed_by_type}/${a.proposed_by_id}`}>
                        {proposedByText(a)}
                      </span>
                    </td>
                    <td className="py-1.5 opacity-70">{a.reason}</td>
                    <td className="py-1.5">
                      {a.status === "proposed" && (
                        <div className="flex flex-wrap gap-1.5">
                          <button
                            type="button"
                            disabled={busyIds.has(a.id) || bulkBusy || !canDecide}
                            title={canDecide ? undefined : `Berechtigung 'actions.approve:${a.risk}' fehlt.`}
                            onClick={() => void approve(a)}
                            className="rounded bg-emerald-500/20 px-2 py-1 text-xs hover:bg-emerald-500/30 disabled:opacity-40"
                          >
                            Bestätigen
                          </button>
                          <button
                            type="button"
                            disabled={busyIds.has(a.id) || bulkBusy || !canDecide}
                            onClick={() => void reject(a)}
                            className="rounded bg-red-500/20 px-2 py-1 text-xs hover:bg-red-500/30 disabled:opacity-40"
                          >
                            Ablehnen
                          </button>
                          <button
                            type="button"
                            disabled={busyIds.has(a.id) || bulkBusy || !canDecide}
                            onClick={() => void dismiss(a)}
                            className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40"
                          >
                            Verwerfen
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
