/** Stack aus einem Git-Repository: Quelle eintragen, den Stand holen, ansehen und über die Freigabe einspielen. */
import { useEffect, useRef, useState } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorText } from "../../../_shared/frontend/src/api";
import { deck } from "../../../_shared/frontend/src/deck";
import { formatBytes } from "../../../_shared/frontend/src/format";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Badge, Button, type Tone } from "../../../_shared/frontend/src/ui";

import { DiffView } from "./DiffView";
import { JSON_HEADERS, withResult } from "./StackEditor";

interface Source { url: string; ref: string; path: string; access: string | null; set_at: string }
interface Access { name: string; host: string }
interface GitOut { source: Source | null; accesses: Access[]; layout_problem: string | null }
interface Applied {
  applied: { commit: string; at: string; files: number } | null;
  previous: { id: string; commit: string; commit_before: string | null; at: string } | null;
}
interface FileState { path: string; server_path: string; state: "new" | "changed" | "same" | "other"; size: number; mode: string; env?: boolean }
export interface Preview {
  commit: { id: string; short: string; ref_kind: "branch" | "tag"; author: string; date: string; message: string };
  ok: boolean;
  problems: string[];
  warnings: string[];
  files: FileState[];
  compose: { path: string; old: string | null; new: string; same: boolean } | null;
  unchanged: boolean;
}

const FIELD = "rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]";
const STATE: Record<FileState["state"], { label: string; tone: Tone }> = {
  new: { label: "neu", tone: "info" },
  changed: { label: "geändert", tone: "warn" },
  same: { label: "gleich", tone: "neutral" },
  other: { label: "keine normale Datei", tone: "bad" },
};
// Eine Umgebungsdatei, die es auf dem Server schon gibt, ersetzt Deck nie.
const KEPT: { label: string; tone: Tone } = { label: "bleibt, wie sie ist", tone: "neutral" };
const shownState = (file: FileState) => (file.env && file.state === "changed" ? KEPT : STATE[file.state]);
const INTRO =
  "Nodvard Deck holt das Repository selbst über HTTPS und vergleicht den Ordner der Compose-Datei mit dem Ordner des Stacks. Holen " +
  "und Ansehen ändern auf dem Server nichts, und aus dem Repository läuft dabei nichts. Einspielen geht erst nach deiner Bestätigung " +
  "und mit Freigabe.";
interface VolumePlan { volumes: Record<string, number | null>; free_bytes: number | null; folder: string | null; error: string | null; services: string[] }
interface BackupChoice { on: boolean; plan: VolumePlan | null; problem: string | null }
const NO_BACKUP: BackupChoice = { on: false, plan: null, problem: null };

function sizeOf(plan: VolumePlan): string {
  const sizes = Object.values(plan.volumes);
  return sizes.every((v) => v != null) ? formatBytes(sizes.reduce<number>((sum, v) => sum + (v ?? 0), 0)) : "Größe unbekannt";
}

const WAITING = "Zum Einspielen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.";
const WAITING_BACK = "Zum Zurückholen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.";
const WAITING_RESTART = "Zum Neustart vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.";

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}

export function PreviewView({ preview, source }: { preview: Preview; source: Source }): JSX.Element {
  const { commit } = preview;
  return (
    <div className="flex flex-col gap-2" data-testid="git-preview">
      <p className="break-words">
        Stand <span className="font-mono">{commit.short}</span> ({commit.ref_kind === "tag" ? "Tag" : "Branch"} {source.ref}) von {commit.author},{" "}
        {when(commit.date)}{commit.message ? `: ${commit.message}` : ""}
      </p>
      {preview.problems.length > 0 && (
        <div role="alert">
          <p className="text-red-400">So ließe sich dieser Stand nicht einspielen:</p>
          <ul className="list-disc pl-5 text-red-300">{preview.problems.map((line, i) => <li key={i} className="break-words">{line}</li>)}</ul>
        </div>
      )}
      {preview.warnings.length > 0 && (
        <div>
          <p className="text-amber-300">Darauf solltest du achten:</p>
          <ul className="list-disc pl-5 text-amber-200">{preview.warnings.map((line, i) => <li key={i} className="break-words">{line}</li>)}</ul>
        </div>
      )}
      {preview.unchanged && <p className="text-emerald-300">Auf dem Server steht schon genau dieser Stand.</p>}
      {preview.files.length > 0 && (
        <ul className="flex flex-col gap-0.5" aria-label="Dateien aus dem Repository">
          {preview.files.map((file) => (
            <li key={file.path} className="flex flex-wrap items-center gap-1.5 break-all">
              <Badge tone={shownState(file).tone}>{shownState(file).label}</Badge>
              <span className="font-mono">{file.server_path}</span>
            </li>
          ))}
        </ul>
      )}
      {preview.compose && preview.compose.old !== null && !preview.compose.same && (
        <DiffView before={preview.compose.old.replace(/\r\n/g, "\n")} after={preview.compose.new} open />
      )}
    </div>
  );
}

export function GitSourcePanel({ hostId, project, onDone }: {
  hostId: string; project: string; onDone: (message: string, warn?: boolean) => void;
}): JSX.Element {
  const [state, setState] = useState<GitOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState({ url: "", ref: "main", path: "compose.yaml", access: "" });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "fetch" | "remove" | "apply" | "back" | "restart" | null>(null);
  const [stale, setStale] = useState<{ services: string[]; text: string; warn: boolean } | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const [backup, setBackup] = useState<BackupChoice>(NO_BACKUP);
  // Jede Wahl und jeder neue Stand zählt hoch; eine Antwort zu einer älteren Wahl gilt nicht mehr.
  const planRequest = useRef(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/git`;

  useEffect(() => {
    const controller = new AbortController();
    setState(null);
    setError(null);
    Promise.all([fetchJson<GitOut>(url, { signal: controller.signal }), fetchJson<Applied>(`${url}/applied`, { signal: controller.signal })])
      .then(([body, done]) => {
        if (controller.signal.aborted) return;
        setState(body);
        setApplied(done);
        if (body.source) setDraft({ url: body.source.url, ref: body.source.ref, path: body.source.path, access: body.source.access ?? "" });
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [url, reload]);

  async function send(kind: "save" | "fetch" | "remove", init: RequestInit): Promise<unknown | null> {
    setBusy(kind);
    setFailed(null);
    try {
      const res = await authedFetch(kind === "fetch" ? `${url}/fetch` : url, init);
      if (!res.ok) throw new Error(await errorText(res));
      return await res.json();
    } catch (err) {
      setFailed(errorMessage(err));
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    const body = { url: draft.url.trim(), ref: draft.ref.trim() || "main", path: draft.path.trim(), access: draft.access || null };
    const saved = (await send("save", { method: "PUT", body: JSON.stringify(body), headers: JSON_HEADERS })) as { source: Source } | null;
    if (!saved || !state) return;
    setState({ ...state, source: saved.source });
    setPreview(null);
    await fetchNow();
  }

  async function fetchNow() {
    const got = (await send("fetch", { method: "POST" })) as Preview | null;
    if (got) setPreview(got);
  }

  /** Nach einem Lauf: auch ein gescheiterter kann einen Teilstand und damit einen Schritt zurück hinterlassen haben. */
  async function refreshApplied() {
    try {
      setApplied(await fetchJson<Applied>(`${url}/applied`));
    } catch {
      // Die alte Anzeige bleibt; „Erneut versuchen“ gibt es beim nächsten Öffnen.
    }
  }

  /** Schlägt vor, gibt frei, wenn der Nutzer das darf, und meldet das Ergebnis. */
  async function propose(kind: "apply" | "back" | "restart", path: string, body: object, waiting: string) {
    setBusy(kind);
    setFailed(null);
    // Scheitert der Neustart, bleibt der Hinweis samt Knopf für einen weiteren Versuch.
    if (kind !== "restart") setStale(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(`${url}/${path}`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      const left = action.result?.detail?.stale;
      if (Array.isArray(left) && left.length > 0 && action.result?.detail?.written === true) {
        // Bleibt offen: Was nicht in der Freigabe stand, startet erst ein eigener Klick neu.
        const warn = outcome.tone !== "success";
        setStale({ services: left.map(String), text: `${action.result.output ?? ""}${warn ? `\n${outcome.text}` : ""}`.trim(), warn });
      } else if (action.status === "proposed") onDone(waiting);
      else if (outcome.tone === "success") onDone(action.result?.output ?? "Erledigt.");
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result?.detail?.written === true) onDone(`${action.result.output ?? ""}\n${outcome.text}`.trim(), true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Was geschehen ist, steht unter „Aktionen“.` : errorMessage(err));
    } finally {
      if (submitted) await refreshApplied();
      setBusy(null);
    }
  }

  useEffect(() => {
    planRequest.current += 1;
    setBackup(NO_BACKUP);
  }, [preview]);

  /** Lädt erst beim Haken, was gesichert würde: Die Größen der Volumes zu lesen kann auf großen Servern dauern. */
  async function chooseBackup(on: boolean) {
    const request = ++planRequest.current;
    if (!on) {
      setBackup(NO_BACKUP);
      return;
    }
    setBackup({ on: true, plan: null, problem: null });
    try {
      const plan = await fetchJson<VolumePlan>(`${url}/volume-plan`);
      if (request === planRequest.current) setBackup(plan.error ? { on: false, plan, problem: plan.error } : { on: true, plan, problem: null });
    } catch (err) {
      if (request === planRequest.current) setBackup({ on: false, plan: null, problem: errorMessage(err) });
    }
  }

  async function apply(shown: Preview) {
    const count = (state: FileState["state"]) => shown.files.filter((f) => f.state === state && shownState(f) === STATE[state]).length;
    const saving = backup.on && backup.plan !== null;
    const stops = backup.plan?.services ?? [];
    const volumes = saving
      ? `Vorher sichert es die Volumes${stops.length > 0 ? `; ${stops.join(", ")} ${stops.length === 1 ? "steht" : "stehen"} dafür still` : ""}. `
      : "Volumes werden vorher nicht gesichert. ";
    const ok = await deck().confirmDialog(
      `Stand ${shown.commit.short} in Stack „${project}“ einspielen? Nodvard Deck schreibt ${count("new")} neue und ${count("changed")} ` +
        "geänderte Dateien auf den Server, Compose wendet den Stack danach an, und Dienste, die geänderte Dateien einlesen, starten neu (die Freigabe nennt sie). Dateien, die es früher aus diesem Repository geschrieben hat und " +
        "die jetzt fehlen, entfernt es nur, wenn sie auf dem Server unverändert sind. Umgebungsdateien wie .env auf dem Server ersetzt es nie. " +
        `${volumes}Der Stand davor bleibt für einen Schritt zurück.`,
      { confirmLabel: "Einspielen" },
    );
    if (ok) await propose("apply", "apply", { commit: shown.commit.id, backup: saving }, WAITING);
  }

  async function back(previous: NonNullable<Applied["previous"]>) {
    const ok = await deck().confirmDialog(
      `Stack „${project}“ zurück auf den Stand vor ${previous.commit.slice(0, 7)}? Nodvard Deck stellt die Dateien wieder her, die das ` +
        "letzte Einspielen ersetzt oder entfernt hat, und nimmt neu angelegte wieder weg, auch eine dabei neu angelegte .env. Was seitdem " +
        "auf dem Server geändert wurde, bleibt, wie es ist. Compose wendet den Stack danach an, und Dienste, die zurückgestellte Dateien einlesen, starten neu " +
        "(die Freigabe nennt sie). Volumes werden vorher nicht gesichert.",
      { confirmLabel: "Zurückholen" },
    );
    if (ok) await propose("back", "rollback", { previous: previous.id }, WAITING_BACK);
  }

  async function restartNow(services: string[]) {
    const ok = await deck().confirmDialog(
      `In Stack „${project}“ neu starten: ${services.join(", ")}? Diese Dienste sind dabei kurz nicht erreichbar und lesen danach den ` +
        "neuen Stand der Dateien.",
      { confirmLabel: "Neu starten" },
    );
    if (ok) await propose("restart", "restart", { services }, WAITING_RESTART);
  }

  async function remove() {
    const ok = await deck().confirmDialog(
      `Repository von „${project}“ vergessen? Nodvard Deck löscht nur die Angaben und den geholten Stand; Stack und Dateien auf dem Server bleiben, wie sie sind.`,
      { confirmLabel: "Vergessen" },
    );
    if (!ok) return;
    if (await send("remove", { method: "DELETE" })) onDone(`Das Repository von „${project}“ ist vergessen.`);
  }

  if (error) return <LoadError what="Die Angaben zum Repository" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!state) return <p className="text-xs opacity-60">Lade die Angaben zum Repository …</p>;
  const source = state.source;
  if (state.layout_problem) {
    return (
      <div className="flex flex-col gap-2 text-xs" data-testid={`git-${project}`}>
        <p className="text-amber-300">{state.layout_problem}</p>
        {source && <div><Button small variant="danger" disabled={busy !== null} onClick={() => void remove()}>Repository vergessen</Button></div>}
        {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
      </div>
    );
  }
  const dirty = !source || source.url !== draft.url.trim() || source.ref !== (draft.ref.trim() || "main") || source.path !== draft.path.trim()
    || (source.access ?? "") !== draft.access;
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`git-${project}`}>
      <p className="opacity-70">{INTRO}</p>
      <label className="flex flex-col gap-1">
        <span>Adresse des Repositorys</span>
        <input aria-label="Adresse des Repositorys" value={draft.url} placeholder="https://github.com/name/repo"
          onChange={(e) => setDraft({ ...draft, url: e.target.value })} className={`${FIELD} font-mono`} />
      </label>
      <div className="flex flex-wrap gap-2">
        <label className="flex min-w-[8rem] flex-1 flex-col gap-1">
          <span>Branch oder Tag</span>
          <input aria-label="Branch oder Tag" value={draft.ref} onChange={(e) => setDraft({ ...draft, ref: e.target.value })} className={`${FIELD} font-mono`} />
        </label>
        <label className="flex min-w-[12rem] flex-[2] flex-col gap-1">
          <span>Compose-Datei im Repository</span>
          <input aria-label="Compose-Datei im Repository" value={draft.path} placeholder="shop/compose.yaml"
            onChange={(e) => setDraft({ ...draft, path: e.target.value })} className={`${FIELD} font-mono`} />
        </label>
      </div>
      <label className="flex flex-col gap-1">
        <span>Zugang</span>
        <select aria-label="Zugang" value={draft.access} onChange={(e) => setDraft({ ...draft, access: e.target.value })} className={FIELD}>
          <option value="">Öffentlich, ohne Anmeldung</option>
          {state.accesses.map((a) => <option key={a.name} value={a.name}>{a.name} ({a.host})</option>)}
          {draft.access && !state.accesses.some((a) => a.name === draft.access) && (
            <option value={draft.access}>{draft.access} (nicht mehr in den Einstellungen)</option>
          )}
        </select>
      </label>
      {state.accesses.length === 0 && (
        <p className="opacity-70">Für ein privates Repository trägst du zuerst einen Zugang mit Token ein: Einstellungen → Erweiterungen → Docker, unter „Erweitert“ bei „Git-Zugänge“.</p>
      )}
      <p className="opacity-70">
        Abgeglichen wird der Ordner, in dem die Compose-Datei im Repository liegt, mit dem Ordner des Stacks; die Compose-Datei selbst mit der des Stacks.
      </p>
      <div className="flex flex-wrap gap-1.5">
        <Button small variant="primary" disabled={busy !== null || !draft.url.trim() || !draft.path.trim() || !dirty} onClick={() => void save()}>
          {busy === "save" ? "…" : source ? "Speichern und holen" : "Eintragen und holen"}
        </Button>
        {source && !dirty && (
          <Button small disabled={busy !== null} onClick={() => void fetchNow()}>{busy === "fetch" ? "Hole …" : "Holen und ansehen"}</Button>
        )}
        {source && <Button small variant="danger" disabled={busy !== null} onClick={() => void remove()}>Repository vergessen</Button>}
      </div>
      {applied?.applied && (
        <p className="opacity-70" data-testid="git-applied">
          Zuletzt eingespielt: Stand <span className="font-mono">{applied.applied.commit.slice(0, 7)}</span>, {when(applied.applied.at)}.
        </p>
      )}
      {applied?.previous && (
        <div>
          <Button small disabled={busy !== null} onClick={() => void back(applied.previous!)}>
            {busy === "back" ? "…" : "Zurück zum Stand davor"}
          </Button>
        </div>
      )}
      {stale && (
        <div className={`space-y-1.5 rounded border p-2 ${stale.warn ? "border-amber-500/50" : "border-white/10"}`} role="status" data-testid="git-stale">
          <p className="whitespace-pre-line break-words">{stale.text}</p>
          <div className="flex flex-wrap gap-1.5">
            <Button small variant="primary" disabled={busy !== null} onClick={() => void restartNow(stale.services)}>
              {busy === "restart" ? "…" : "Jetzt neu starten"}
            </Button>
            <Button small disabled={busy !== null} onClick={() => onDone(stale.text, stale.warn)}>Später</Button>
          </div>
        </div>
      )}
      {busy === "fetch" && <p className="opacity-70" role="status">Hole den Stand aus dem Repository und vergleiche ihn mit dem Server …</p>}
      {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
      {preview && source && <PreviewView preview={preview} source={source} />}
      {preview && source && preview.ok && !preview.unchanged && !dirty && !stale && (
        <div className="flex flex-col gap-1.5">
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={backup.on} disabled={busy !== null} onChange={(e) => void chooseBackup(e.target.checked)} />
            <span className="break-all">
              Volumes vorher sichern{backup.on && backup.plan ? ` (${Object.keys(backup.plan.volumes).join(", ")}: ${sizeOf(backup.plan)}${
                backup.plan.free_bytes != null ? `, frei ${formatBytes(backup.plan.free_bytes)}` : ""})` : ""}
            </span>
          </label>
          {backup.on && !backup.plan && <p className="opacity-70" role="status">Lese die Volumes und den freien Platz …</p>}
          {backup.problem && <p className="opacity-70">{backup.problem}</p>}
          {backup.on && backup.plan && (
            <p className="opacity-70">
              {backup.plan.services.length > 0 && `${backup.plan.services.join(", ")} ${backup.plan.services.length === 1 ? "steht" : "stehen"} dafür still, bis Compose den Stack anwendet. `}
              Die Archive kommen nach {backup.plan.folder ?? "…"}; zurückspielen lassen sie sich im Reiter Stacks unter „Sicherungen“.
            </p>
          )}
          <div>
            <Button small variant="primary" disabled={busy !== null || (backup.on && !backup.plan)} onClick={() => void apply(preview)}>
              {busy === "apply" ? "Spiele ein …" : "Einspielen"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
