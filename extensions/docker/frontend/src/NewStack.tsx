/** Neuer Stack: Name, Ordner und Compose-Datei aus einer Vorlage, einem Beispiel, eingefügtem Text oder einer Datei; angelegt wird über die Freigabe. */
import { useEffect, useRef, useState, type ChangeEvent } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { deck } from "../../../_shared/frontend/src/deck";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Button } from "../../../_shared/frontend/src/ui";

import { CheckResult, JSON_HEADERS, withResult, type CheckOut } from "./StackEditor";
import { StackForm } from "./StackForm";

interface Place {
  root: string;
  default: string;
  root_state: "dir" | "missing" | "link" | "file" | "unknown";
  root_problem: string | null;
  setting_problem: string | null;
  home: string | null;
}
type NewCheck = CheckOut & { folder: string; conflict: string | null };
export interface StackTemplate { id: string; name: string; summary: string; compose: string; note: string; ports: string[] }

export const EXAMPLE = `services:
  app:
    image: nginx:1.27
    restart: unless-stopped
    ports:
      - "8080:80"
`;
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const MAX_BYTES = 256 * 1024;
const RISKY_NOTE = "Das Anlegen braucht dafür eine Freigabe mit hohem Risiko.";
const WAITING = "Zum Anlegen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.";

export function NewStack({ hostId, onClose }: { hostId: string; onClose: (message: string | null) => void }): JSX.Element {
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}`;
  const [place, setPlace] = useState<Place | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [name, setName] = useState("");
  const [text, setText] = useState(EXAMPLE);
  const [checked, setChecked] = useState<NewCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [rootDraft, setRootDraft] = useState<string | null>(null);
  const [asForm, setAsForm] = useState(false);
  const [templates, setTemplates] = useState<StackTemplate[] | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [templateReload, setTemplateReload] = useState(0);
  const [picked, setPicked] = useState<StackTemplate | null>(null);
  const checkRun = useRef(0);
  const life = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    life.current = controller;
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    fetchJson<Place>(`${base}/new-stack`, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setPlace(body); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [base, reload]);

  useEffect(() => {
    const controller = new AbortController();
    setTemplateError(null);
    fetchJson<{ templates: StackTemplate[] }>("/ext/docker/stack-templates", { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setTemplates(body.templates); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setTemplateError(errorMessage(err)); });
    return () => controller.abort();
  }, [templateReload]);

  if (error) return <LoadError what="Der Ordner für neue Stacks" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!place) return <p className="text-xs opacity-60">Lade …</p>;
  const nameOk = NAME_RE.test(name);
  const changed = name !== "" || text !== EXAMPLE;

  function edit(next: { name?: string; text?: string }) {
    checkRun.current += 1;
    if (next.name !== undefined) setName(next.name);
    if (next.text !== undefined) setText(next.text);
    setChecked(null);
    setProblem(null);
  }

  async function pick(id: string) {
    const template = templates?.find((t) => t.id === id);
    if (!template) return;
    const untouched = text === EXAMPLE || text === picked?.compose || text.trim() === "";
    if (!untouched && !(await deck().confirmDialog("Den bisherigen Text durch die Vorlage ersetzen?", { confirmLabel: "Ersetzen" }))) return;
    setPicked(template);
    edit({ text: template.compose, ...(name === "" || name === picked?.id ? { name: template.id } : {}) });
  }

  async function leave() {
    if (changed && !(await deck().confirmDialog("Der neue Stack ist noch nicht angelegt. Verwerfen?", { confirmLabel: "Verwerfen" }))) return;
    onClose(null);
  }

  async function check(): Promise<NewCheck | null> {
    const run = ++checkRun.current;
    setBusy(true);
    setProblem(null);
    try {
      const result = await fetchJson<NewCheck>(`${base}/new-stack/check`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name, content: text }) });
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

  async function create(start: boolean) {
    const result = await check();
    if (!result?.ok || result.conflict) return;
    const risky = result.warnings.length ? ` Riskant: ${result.warnings.join(" ")} ${RISKY_NOTE}` : "";
    const then = start ? " und starten" : "";
    const question = `Stack „${name}“ in ${result.folder} anlegen${then}? Nodvard Deck legt den Ordner an, schreibt compose.yaml und übernimmt den Stack.${risky}`;
    if (!(await deck().confirmDialog(question, { confirmLabel: start ? "Anlegen und starten" : "Anlegen" }))) return;
    setBusy(true);
    const signal = (life.current ?? new AbortController()).signal;
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(`${base}/new-stack`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name, content: text, start }) });
      submitted = true;
      const run = await settleAction(proposal, { signal });
      const action = await withResult(run.action, signal);
      if (signal.aborted) return;
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onClose(WAITING);
      else if (action.result?.detail?.created === true) {
        onClose(outcome.tone === "success" ? action.result.output ?? "Angelegt." : `${action.result.output ?? ""}\n${outcome.text}`.trim());
      } else if (outcome.tone === "pending") onClose(run.text);
      else setProblem(outcome.text);
    } catch (err) {
      if (!signal.aborted) {
        setProblem(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Ob angelegt wurde, steht unter „Aktionen“.` : errorMessage(err));
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }

  function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setProblem(`Die Datei ist größer als ${MAX_BYTES / 1024} KB.`);
      return;
    }
    void file.text().then((content) => {
      setPicked(null);
      edit({ text: content.replace(/\r\n/g, "\n") });
    });
  }

  async function saveRoot(path: string | null) {
    setBusy(true);
    setProblem(null);
    try {
      const body = await fetchJson<{ root: string }>(`${base}/stack-root`, { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ path }) });
      setRootDraft(null);
      setChecked(null);
      setPlace({ ...place!, root: body.root, root_state: "unknown", root_problem: null, setting_problem: null });
      setReload((n) => n + 1);
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 text-xs" data-testid="new-stack">
      <p className="opacity-70">
        Ein neuer Stack bekommt einen eigenen Ordner. Nodvard Deck legt ihn erst nach der Freigabe an, schreibt darin compose.yaml und überschreibt
        nie einen bestehenden Ordner.
      </p>
      {rootDraft === null ? (
        <p className="flex flex-wrap items-center gap-2">
          <span className="break-all">
            Ordner für neue Stacks: <span className="font-mono">{place.root}</span>
            {place.root_state === "missing" && " (gibt es noch nicht, wird mit angelegt)"}
          </span>
          <Button small onClick={() => setRootDraft(place.root)}>Ändern</Button>
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input aria-label="Ordner für neue Stacks" value={rootDraft} onChange={(e) => setRootDraft(e.target.value)}
            className="min-w-0 flex-1 rounded border border-white/10 bg-black/25 px-2 py-1 font-mono outline-none focus:border-[var(--color-accent)]" />
          <Button small variant="primary" disabled={busy} onClick={() => void saveRoot(rootDraft.trim())}>Übernehmen</Button>
          {place.root !== place.default && <Button small disabled={busy} onClick={() => void saveRoot(null)}>Vorgabe ({place.default})</Button>}
          {place.home && <Button small disabled={busy} onClick={() => setRootDraft(`${place.home}/stacks`)}>Im Heimatordner</Button>}
          <Button small onClick={() => setRootDraft(null)}>Abbrechen</Button>
        </div>
      )}
      {place.setting_problem && <p className="text-amber-300" role="status">{place.setting_problem}</p>}
      {place.root_problem && <p className="text-amber-300" role="status">{place.root_problem}</p>}
      {templateError ? <LoadError what="Die Vorlagen" error={templateError} onRetry={() => setTemplateReload((n) => n + 1)} compact /> : templates && (
        <label className="flex flex-col gap-1">
          <span>Vorlage</span>
          <select aria-label="Vorlage" value={picked?.id ?? ""} disabled={busy} onChange={(e) => void pick(e.target.value)}
            className="rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]">
            <option value="" disabled>Ohne Vorlage</option>
            {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
      )}
      {picked && (
        <div className="flex flex-col gap-1 rounded border border-white/10 bg-white/[0.03] p-2" data-testid="template-note">
          <p>{picked.summary}</p>
          <p className="opacity-70">{picked.note}</p>
          <p className="opacity-70">Belegt auf dem Server {picked.ports.length === 1 ? "den Port" : "die Ports"} {picked.ports.join(", ")}.</p>
          {text !== picked.compose && <p className="text-amber-300">Du hast die Datei geändert; die Angaben oben gelten für die Vorlage, wie sie war.</p>}
        </div>
      )}
      <label className="flex flex-col gap-1">
        <span>Name</span>
        <input aria-label="Name des Stacks" value={name} onChange={(e) => edit({ name: e.target.value.trim() })} placeholder="zum Beispiel vaultwarden"
          className="rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]" />
      </label>
      {name && !nameOk && <p className="text-amber-300">Nur Kleinbuchstaben, Ziffern, „-“ und „_“, am Anfang ein Buchstabe oder eine Ziffer.</p>}
      {nameOk && <p className="break-all opacity-70">Ordner: <span className="font-mono">{place.root}/{name}</span></p>}
      <div className="flex flex-wrap gap-1.5">
        <Button small onClick={() => { setPicked(null); edit({ text: EXAMPLE }); }}>Beispiel einfügen</Button>
        <label className="cursor-pointer rounded-lg border border-white/10 bg-white/[0.06] px-2 py-1 hover:bg-white/[0.12]">
          Datei hochladen
          <input type="file" accept=".yml,.yaml,text/yaml" className="hidden" aria-label="Compose-Datei hochladen" onChange={upload} />
        </label>
        <span className="flex-1" />
        <Button small pressed={!asForm} onClick={() => setAsForm(false)}>Text</Button>
        <Button small pressed={asForm} onClick={() => setAsForm(true)}>Formular</Button>
      </div>
      {asForm ? <StackForm text={text} onChange={(next) => edit({ text: next })} disabled={busy} /> : (
        <textarea aria-label="Compose-Datei des neuen Stacks" value={text} spellCheck={false} wrap="off" rows={16}
          className="w-full resize-y rounded bg-black/60 p-2 font-mono text-[11px] leading-snug outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
          onChange={(e) => edit({ text: e.target.value })} />
      )}
      {checked?.conflict && <p className="text-red-400" role="alert">{checked.conflict}</p>}
      <CheckResult checked={checked} note={RISKY_NOTE} />
      {problem && <p className="break-words text-red-400" role="alert">{problem}</p>}
      <div className="flex flex-wrap gap-1.5">
        <Button small disabled={busy || !nameOk} onClick={() => void check()}>Prüfen</Button>
        <Button small variant="primary" disabled={busy || !nameOk} onClick={() => void create(false)}>Anlegen</Button>
        <Button small disabled={busy || !nameOk} onClick={() => void create(true)}>Anlegen und starten</Button>
        <Button small onClick={() => void leave()}>Schließen</Button>
      </div>
    </div>
  );
}
