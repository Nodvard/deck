/** Editor für die Compose-Dateien eines übernommenen Stacks: prüfen ohne zu speichern, speichern über die Freigabe, alte Fassungen. */
import { useEffect, useRef, useState } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { deck } from "../../../_shared/frontend/src/deck";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Button } from "../../../_shared/frontend/src/ui";

import { DiffView } from "./DiffView";
import { StackForm } from "./StackForm";

interface EditFile { path: string; content: string | null; error: string | null; sha256?: string; edit_error?: string | null }
export interface CheckOut { ok: boolean; error: string | null; unverified?: string | null; warnings: string[]; services: string[] }
interface HistoryEntry { id: string; path: string; saved_at: string; size: number }

export const JSON_HEADERS = { "Content-Type": "application/json" };
const RISKY_NOTE = "Kommt so eine Einstellung neu dazu, braucht das Speichern eine Freigabe mit hohem Risiko.";
const WAITING = "Zum Speichern vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.";
const DONE_STATES = new Set(["succeeded", "failed", "denied", "expired", "dismissed"]);

function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function editable(file: EditFile | undefined): file is EditFile & { content: string; sha256: string } {
  return !!file && file.content != null && !!file.sha256 && !file.edit_error;
}

/** Das Textfeld kennt nur `\n`; eine Datei mit Windows-Zeilenenden bekommt sie beim Speichern zurück. */
export function forServer(text: string, original: string): string {
  return original.includes("\r\n") ? text.replace(/\r?\n/g, "\r\n") : text;
}

/** „16.10.2026, 02:24“ */
export function savedAt(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}

/** Die Aktion mit Ergebnis; lief sie gleich durch (volle Selbstständigkeit), fehlt es in der ersten Antwort. */
export async function withResult(action: ActionState, signal?: AbortSignal): Promise<ActionState> {
  const id = action.id ?? action.action_id;
  if (action.result || !id || !DONE_STATES.has(action.status ?? "")) return action;
  return { ...action, ...(await fetchJson<ActionState>(`/actions/${id}`, { signal })) };
}

/** Was Compose zur Datei sagt: abgelehnt, nicht geprüft oder lesbar, dazu die riskanten Einstellungen. */
export function CheckResult({ checked, note }: { checked: CheckOut | null; note: string }): JSX.Element | null {
  if (!checked) return null;
  if (checked.error) return <p className="text-red-400" role="alert">Compose lehnt die Datei ab: {checked.error}</p>;
  if (checked.unverified) return <p className="text-amber-300" role="alert">Nicht geprüft: {checked.unverified}</p>;
  return (
    <>
      <p className="text-emerald-300" role="status">Compose kann die Datei lesen{checked.services.length ? ` (Dienste: ${checked.services.join(", ")})` : ""}.</p>
      {checked.warnings.length > 0 && (
        <div className="text-amber-300" data-testid="stack-editor-warnings">
          <p>Riskante Einstellungen:</p>
          <ul className="list-disc pl-5">{checked.warnings.map((w) => <li key={w} className="break-words">{w}</li>)}</ul>
          <p>{note}</p>
        </div>
      )}
    </>
  );
}

export function StackEditor({ hostId, project, onClose, onDirty }: {
  hostId: string;
  project: string;
  onClose: (message: string | null) => void;
  /** Meldet offene Änderungen, damit die Liste vor dem Schließen fragen kann. */
  onDirty?: (dirty: boolean) => void;
}): JSX.Element {
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}`;
  const [files, setFiles] = useState<EditFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [index, setIndex] = useState(0);
  const [text, setText] = useState("");
  const [checked, setChecked] = useState<CheckOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [asForm, setAsForm] = useState(false);
  const keep = useRef<{ path: string; text: string } | null>(null);
  const checkRun = useRef(0);
  const life = useRef<AbortController | null>(null);

  // Im Effekt angelegt, nicht beim ersten Rendern: StrictMode baut den Effekt zweimal auf und ab.
  useEffect(() => {
    const controller = new AbortController();
    life.current = controller;
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setFiles(null);
    setError(null);
    fetchJson<{ files: EditFile[] }>(`${base}/file`, { signal: controller.signal })
      .then((body) => {
        if (controller.signal.aborted) return;
        const kept = keep.current;
        keep.current = null;
        const wanted = kept ? body.files.findIndex((f) => f.path === kept.path && editable(f)) : -1;
        const first = wanted >= 0 ? wanted : Math.max(0, body.files.findIndex(editable));
        setFiles(body.files);
        setIndex(first);
        setText(wanted >= 0 && kept ? kept.text : (body.files[first]?.content ?? "").replace(/\r\n/g, "\n"));
        setChecked(null);
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [base, reload]);

  const file = files?.[index];
  const dirty = editable(file) && forServer(text, file.content) !== file.content;
  useEffect(() => { onDirty?.(dirty); }, [dirty, onDirty]);

  if (error) return <LoadError what="Die Compose-Datei" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!files) return <p className="text-xs opacity-60">Lade die Datei …</p>;
  if (!editable(file)) {
    return (
      <div className="flex flex-col gap-2 text-xs">
        <p className="text-amber-300">Diese Datei lässt sich hier nicht bearbeiten: {file?.edit_error ?? file?.error ?? "Docker nennt keine lesbare Datei."}</p>
        <div><Button small onClick={() => onClose(null)}>Schließen</Button></div>
      </div>
    );
  }
  const current = file;
  const name = baseName(current.path);
  const content = forServer(text, current.content);

  async function leave(next: () => void) {
    if (dirty && !(await deck().confirmDialog("Deine Änderung ist noch nicht gespeichert. Verwerfen?", { confirmLabel: "Verwerfen" }))) return;
    next();
  }

  function edit(value: string) {
    checkRun.current += 1;
    setText(value);
    setChecked(null);
    setNote(null);
  }

  async function check(): Promise<CheckOut | null> {
    const run = ++checkRun.current;
    setBusy(true);
    setProblem(null);
    setNote(null);
    try {
      const result = await fetchJson<CheckOut>(`${base}/check`, {
        method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ path: current.path, content }),
      });
      if (run !== checkRun.current) return null;
      setChecked(result);
      return result;
    } catch (err) {
      if (run === checkRun.current) setProblem(errorMessage(err));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function save(apply: boolean) {
    const result = await check();
    if (!result?.ok) return;
    const risky = result.warnings.length ? ` Riskant: ${result.warnings.join(" ")} ${RISKY_NOTE}` : "";
    const then = apply ? " und den Stack danach anwenden" : "";
    const pause = apply ? " Geänderte Dienste werden dabei neu erstellt und sind kurz nicht erreichbar." : "";
    const question = `„${name}“ von Stack „${project}“ speichern${then}? Die alte Fassung bleibt im Verlauf.${pause}${risky}`;
    if (!(await deck().confirmDialog(question, { confirmLabel: apply ? "Speichern und anwenden" : "Speichern" }))) return;
    setBusy(true);
    const signal = (life.current ?? new AbortController()).signal;
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(`${base}/save`, {
        method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ path: current.path, content, base_sha256: current.sha256, apply }),
      });
      submitted = true;
      const run = await settleAction(proposal, { signal });
      const action = await withResult(run.action, signal);
      if (signal.aborted) return;
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onClose(WAITING);
      else if (action.result?.detail?.saved === true) {
        onClose(outcome.tone === "success" ? action.result.output ?? "Gespeichert." : `${action.result.output ?? ""}\n${outcome.text}`.trim());
      } else if (outcome.tone === "pending") onClose(run.text);
      else setProblem(outcome.text);
    } catch (err) {
      if (!signal.aborted) {
        setProblem(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Ob gespeichert wurde, steht unter „Aktionen“.` : errorMessage(err));
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }

  async function toggleHistory() {
    if (history) {
      setHistory(null);
      return;
    }
    setHistoryError(null);
    try {
      setHistory((await fetchJson<{ entries: HistoryEntry[] }>(`${base}/history`)).entries);
    } catch (err) {
      setHistoryError(errorMessage(err));
    }
  }

  async function loadOld(entry: HistoryEntry) {
    const target = files!.findIndex((f) => f.path === entry.path && editable(f));
    if (target < 0) {
      setProblem(`Die alte Fassung gehört zu ${entry.path}, und diese Datei lässt sich hier gerade nicht bearbeiten.`);
      return;
    }
    await leave(async () => {
      try {
        const old = await fetchJson<{ content: string }>(`${base}/history/${encodeURIComponent(entry.id)}`);
        setIndex(target);
        edit(old.content.replace(/\r\n/g, "\n"));
        setProblem(null);
        setNote(`Die Fassung vom ${savedAt(entry.saved_at)} steht jetzt im Editor. Gespeichert ist sie erst nach „Speichern“.`);
      } catch (err) {
        setProblem(errorMessage(err));
      }
    });
  }

  function reloadKeeping() {
    keep.current = { path: current.path, text };
    setProblem(null);
    setNote("Die Datei auf dem Server hat sich geändert. Dein Speichern ersetzt diese neue Fassung; sie steht danach im Verlauf.");
    setReload((n) => n + 1);
  }

  const switchable = files.map((f, i) => ({ f, i })).filter(({ f }) => editable(f));
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`stack-editor-${project}`}>
      <p className="opacity-70">
        Auf den Server kommt die Änderung erst mit „Speichern“ und der Freigabe. Vorher prüft Compose die Datei; die alte Fassung bleibt im Verlauf.
      </p>
      {switchable.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {switchable.map(({ f, i }) => (
            <Button key={f.path} small pressed={i === index}
              onClick={() => void leave(() => { setIndex(i); edit((f.content ?? "").replace(/\r\n/g, "\n")); })}>
              {baseName(f.path)}
            </Button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="flex-1 break-all font-mono opacity-80">{current.path}</span>
        <Button small pressed={!asForm} onClick={() => setAsForm(false)}>Text</Button>
        <Button small pressed={asForm} onClick={() => setAsForm(true)}>Formular</Button>
      </div>
      {asForm ? <StackForm text={text} onChange={edit} disabled={busy} /> : (
        <textarea
          aria-label={`Inhalt von ${name}`} value={text} spellCheck={false} wrap="off" rows={22}
          className="w-full resize-y rounded bg-black/60 p-2 font-mono text-[11px] leading-snug outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
          onChange={(event) => edit(event.target.value)}
        />
      )}
      <DiffView key={`${current.path}-${asForm}`} before={current.content.replace(/\r\n/g, "\n")} after={text} open={asForm} />
      <CheckResult checked={checked} note={`${RISKY_NOTE} Was schon in der Datei auf dem Server steht, zählt dabei nicht noch einmal.`} />
      {note && <p className="opacity-80" role="status">{note}</p>}
      {problem && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="break-words text-red-400" role="alert">{problem}</p>
          <Button small onClick={reloadKeeping}>Stand vom Server holen, meine Fassung behalten</Button>
          <Button small onClick={() => void leave(() => setReload((n) => n + 1))}>Datei neu laden</Button>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button small disabled={busy} onClick={() => void check()}>Prüfen</Button>
        <Button small variant="primary" disabled={busy || !dirty} onClick={() => void save(false)}>Speichern</Button>
        <Button small disabled={busy || !dirty} onClick={() => void save(true)}>Speichern und anwenden</Button>
        <Button small pressed={history !== null} onClick={() => void toggleHistory()}>Verlauf</Button>
        <Button small onClick={() => void leave(() => onClose(null))}>Schließen</Button>
      </div>
      {historyError && <LoadError what="Der Verlauf" error={historyError} onRetry={() => { setHistory(null); void toggleHistory(); }} compact />}
      {history && (
        <div data-testid="stack-editor-history">
          {history.length === 0 ? (
            <p className="opacity-70">Noch keine alte Fassung: Der Verlauf füllt sich mit jedem Speichern.</p>
          ) : (
            <ul className="divide-y divide-white/5">
              {history.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-center gap-2 py-1">
                  <span className="flex-1 break-all">{savedAt(entry.saved_at)} · {baseName(entry.path)} · {entry.size} Bytes</span>
                  <Button small onClick={() => void loadOld(entry)}>In den Editor laden</Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
