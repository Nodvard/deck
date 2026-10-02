/**
 * Image-Updates einspielen: der Knopf in der Spalte "Image-Update" und die Uebersicht davor.
 *
 * Ablauf: "Einspielen" oeffnet unter der Zeile eine Uebersicht (`GET .../image-update/plan`,
 * nur lesend): welches Image, welcher Befehl laeuft auf dem Host, was ist betroffen, welche
 * Warnungen gibt es, wie geht es zurueck. Erst "Jetzt einspielen" legt die Gate-Aktion
 * `container.image_update` an (`POST .../image-update` mit der `plan_id` genau dieser
 * Uebersicht). Wer `actions.approve:<risk>` hat, gibt selbst frei (die Uebersicht IST die
 * menschliche Bestaetigung -- deshalb kein Dialog: `confirmDialog` zeigt nur kurzen Text und
 * koennte den Befehl nicht anzeigen), sonst bleibt es ein Vorschlag fuer einen Admin.
 *
 * Der Lauf selbst geschieht auf dem Server (entkoppelt); die Seite fragt nur nach.
 */
import { useEffect, useState } from "react";

import { settleAction } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody, errorText } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { deck } from "../../../_shared/frontend/src/deck";

/** Was die Pruefung ueber die Einspielbarkeit weiss (nur aus Etiketten; endgueltig entscheidet die Uebersicht). */
export interface ImageApply {
  mode: "compose" | "none";
  kind?: string;
  why?: string | null;
  project?: string;
  service?: string;
}

export type ApplyPhase = "start" | "pull" | "up" | "verify";
export interface ApplyingState {
  phase: ApplyPhase;
  started_at: string;
  run_id: string;
}
export interface AppliedState {
  ok: boolean;
  summary: string;
  finished_at: string;
}

export interface UpdatePlan {
  ok: true;
  plan_id: string;
  host_id: string;
  container: string;
  image: string;
  current_short: string;
  remote_short: string;
  registry_at: string | null;
  stale: boolean;
  project: string;
  service: string;
  affected: string[];
  command: string;
  rollback: string;
  risk: "low" | "medium" | "high";
  warnings: string[];
}
type PlanReply = UpdatePlan | { ok: false; kind: string; reason: string };

const PHASE_TEXT: Record<ApplyPhase, string> = {
  start: "startet …",
  pull: "lädt Image …",
  up: "erstellt Container neu …",
  verify: "prüft Start …",
};
const DOWNTIME_TEXT = "Der Container wird neu erstellt und ist dabei kurz nicht erreichbar (meist unter einer Minute; das Herunterladen vorher kann dauern).";

function timeOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const BUTTON = "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";

/** Unter dem Badge: "Einspielen", der Grund, warum es nicht geht, oder der Stand eines laufenden/beendeten Updates. */
export function ImageUpdateAction({
  entryId, offer, apply, applying, applied, open, onToggle,
}: {
  entryId: string;
  /** Darf hier ueberhaupt angeboten werden (verwaltbar, laeuft, nicht Nodvard Deck selbst, Status "Update", hosts.execute). */
  offer: boolean;
  apply?: ImageApply;
  applying?: ApplyingState;
  applied?: AppliedState;
  open: boolean;
  onToggle: () => void;
}): JSX.Element | null {
  const parts: JSX.Element[] = [];
  if (applying) {
    parts.push(<span key="run" className="block text-amber-300" role="status">Update läuft – {PHASE_TEXT[applying.phase] ?? PHASE_TEXT.start}</span>);
  } else {
    if (applied) {
      const at = timeOf(applied.finished_at);
      parts.push(
        applied.ok
          ? <span key="done" className="block text-emerald-300" title={applied.summary}>✓ eingespielt {at}</span>
          : <span key="failed" className="block text-red-300" title={applied.summary}>Update fehlgeschlagen {at}</span>,
      );
    }
    if (offer && apply?.mode === "none") {
      parts.push(<span key="why" className="mt-0.5 block max-w-[15rem] whitespace-normal break-words text-[11px] opacity-60" title={apply.why ?? undefined}>{apply.why}</span>);
    } else if (offer) {
      parts.push(
        <button key="btn" type="button" aria-expanded={open} onClick={onToggle}
          title="Neues Image laden und Container neu erstellen – vorher kommt eine Übersicht"
          className={`mt-1 ${BUTTON} ${open ? "bg-white/20" : ""}`}>
          Einspielen
        </button>,
      );
    }
  }
  if (parts.length === 0) return null;
  return <div className="mt-1" data-testid={`image-action-${entryId}`}>{parts}</div>;
}

type Run =
  | { state: "idle" }
  | { state: "posting" }
  | { state: "proposed" }
  | { state: "done"; ok: boolean; text: string; output: string | null }
  | { state: "error"; message: string; conflict: boolean };

/** Die Uebersicht vor dem Einspielen und, nach dem Klick, der Verlauf bis zum Ergebnis. */
export function UpdatePanel({
  hostId, container, applying, onStarted, onFinished, onClose,
}: {
  hostId: string;
  container: string;
  applying?: ApplyingState;
  /** Der Vorschlag ist angelegt bzw. freigegeben: den Stand der Image-Updates sofort neu laden, damit
   * "Update läuft" erscheint und der Knopf in der Zeile verschwindet (nicht erst nach dem naechsten Takt). */
  onStarted: () => void;
  /** Nach einem Ergebnis (Erfolg oder Fehler): Seite und Image-Stand neu laden. */
  onFinished: () => void;
  onClose: () => void;
}): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [plan, setPlan] = useState<PlanReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<Run>({ state: "idle" });
  const [reload, setReload] = useState(0);
  // Solange die Freigabe/der Start noch wartet (bis zu 20 s), weiter nachfragen.
  useEffect(() => {
    if (run.state !== "posting") return;
    const interval = setInterval(onStarted, 2000);
    return () => clearInterval(interval);
  }, [run.state, onStarted]);
  const base = `/ext/service-matrix/containers/${hostId}/${encodeURIComponent(container)}/image-update`;

  useEffect(() => {
    let cancelled = false;
    setPlan(null);
    setError(null);
    setRun({ state: "idle" });
    authedFetch(`${base}/plan`)
      .then(async (res) => {
        if (!res.ok) throw new Error(await errorText(res));
        return res.json() as Promise<PlanReply>;
      })
      .then((body) => { if (!cancelled) setPlan(body); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, [base, reload]);

  async function start(p: UpdatePlan) {
    setRun({ state: "posting" });
    try {
      const res = await authedFetch(base, { method: "POST", body: JSON.stringify({ plan_id: p.plan_id }), signal: unmountSignal() });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setRun({ state: "error", message: errorFromBody(body, res.status), conflict: res.status === 409 });
        return;
      }
      onStarted();
      // Selbst freigeben, wenn erlaubt (die Uebersicht war die Bestaetigung) -- hier statt in
      // `settleAction`, damit der Stand gleich danach neu geladen werden kann.
      let action = body as { id?: string; action_id?: string; status?: string; risk?: string };
      let approvedHere = false;
      const id = action.id ?? action.action_id;
      if (action.status === "proposed" && id && deck().hasPermission(`actions.approve:${action.risk ?? p.risk}`)) {
        const approveRes = await authedFetch(`/actions/${id}/approve`, { method: "POST", signal: unmountSignal() });
        const approveBody = await approveRes.json().catch(() => ({}));
        if (!approveRes.ok) throw new Error(errorFromBody(approveBody, approveRes.status));
        action = { ...action, ...(approveBody as object) };
        approvedHere = true;
        onStarted();
      }
      const outcome = await settleAction(action, { risk: p.risk, approve: false, signal: unmountSignal() });
      const status = outcome.action.status;
      if (!approvedHere && status === "proposed") {
        setRun({ state: "proposed" });
        return;
      }
      if (status === "succeeded") {
        const output = outcome.action.result?.output ?? null;
        setRun({ state: "done", ok: true, text: `Fertig: ${(output ?? "").split("\n")[0] || "eingespielt"}`, output });
      } else if (status === "failed") {
        setRun({ state: "done", ok: false, text: `Fehlgeschlagen: ${outcome.action.result?.error ?? "unbekannter Fehler"}`, output: outcome.action.result?.output ?? null });
      } else {
        setRun({ state: "done", ok: outcome.tone !== "error", text: outcome.text, output: null });
      }
      onFinished();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setRun({ state: "error", message: err instanceof Error ? err.message : String(err), conflict: false });
    }
  }

  return (
    <div className="sticky left-0 max-w-[calc(100vw-3rem)] text-xs sm:max-w-none" data-testid={`update-panel-${hostId}:${container}`}>
      {error && <p className="text-red-400">Übersicht nicht abrufbar: {error}</p>}
      {!plan && !error && <p className="opacity-60">Lade Übersicht …</p>}
      {plan && !plan.ok && (
        <div>
          <p className="opacity-90">{plan.reason}</p>
          <button type="button" onClick={onClose} className={`mt-2 ${BUTTON}`}>Schließen</button>
        </div>
      )}
      {plan?.ok && (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Update für „{plan.container}“ einspielen</p>
          <dl className="grid gap-x-6 gap-y-1 opacity-85 sm:grid-cols-2">
            <div><dt className="opacity-60">Image</dt><dd className="break-words">{plan.image}</dd></div>
            <div>
              <dt className="opacity-60">Version</dt>
              <dd>
                Image-ID {plan.current_short} · neue Version bei der Registry (Prüfsumme {plan.remote_short || "?"})
                {plan.registry_at ? `, Stand ${new Date(plan.registry_at).toLocaleString()}` : ""}
              </dd>
            </div>
            <div><dt className="opacity-60">Compose</dt><dd className="break-words">{plan.project}/{plan.service}</dd></div>
            {plan.affected.length > 1 && <div><dt className="opacity-60">Betrifft</dt><dd className="break-words">{plan.affected.join(", ")}</dd></div>}
          </dl>
          <div className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-amber-200">
            <p>{DOWNTIME_TEXT}</p>
            {plan.warnings.length > 0 && (
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {plan.warnings.map((w) => <li key={w} className={w.startsWith("Datenbank") ? "font-medium text-red-300" : undefined}>{w}</li>)}
              </ul>
            )}
          </div>
          <details open>
            <summary className="cursor-pointer opacity-70">Befehl auf dem Server</summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]">{plan.command}</pre>
          </details>
          <details>
            <summary className="cursor-pointer opacity-70">Zurück zur alten Version</summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]">{plan.rollback}</pre>
          </details>

          {run.state === "idle" && (
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              {deck().hasPermission(`actions.approve:${plan.risk}`) ? (
                <button type="button" onClick={() => void start(plan)}
                  className={`rounded px-3 py-1.5 text-xs font-medium ${plan.risk === "high" ? "bg-red-500/80 hover:bg-red-500" : "bg-[var(--color-accent)] hover:opacity-90"}`}>
                  Jetzt einspielen
                </button>
              ) : (
                <>
                  <button type="button" onClick={() => void start(plan)} className={BUTTON}>Vorschlagen</button>
                  <span className="opacity-70">Ein Admin muss unter „Aktionen“ freigeben.</span>
                </>
              )}
              <button type="button" onClick={onClose} className={BUTTON}>Abbrechen</button>
            </div>
          )}
          {run.state === "posting" && (
            <p className="text-amber-300" role="status">Läuft … {applying ? PHASE_TEXT[applying.phase] : "wird gestartet …"}</p>
          )}
          {run.state === "proposed" && (
            <p role="status">Vorgeschlagen – Freigabe durch einen Admin nötig, siehe „Aktionen“.</p>
          )}
          {run.state === "done" && (
            <div role="status">
              <p className={run.ok ? "text-emerald-300" : "text-red-300"}>{run.text}</p>
              {run.output && (
                run.ok ? (
                  <details className="mt-1">
                    <summary className="cursor-pointer opacity-70">Protokoll und Rückweg</summary>
                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]">{run.output}</pre>
                  </details>
                ) : (
                  <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]">{run.output}</pre>
                )
              )}
            </div>
          )}
          {run.state === "error" && (
            <div role="alert">
              <p className="text-red-300">{run.message}</p>
              {run.conflict && <button type="button" onClick={() => setReload((n) => n + 1)} className={`mt-2 ${BUTTON}`}>Neu laden</button>}
            </div>
          )}
          {(run.state === "proposed" || run.state === "done" || run.state === "error") && (
            <div><button type="button" onClick={onClose} className={BUTTON}>Schließen</button></div>
          )}
        </div>
      )}
    </div>
  );
}
