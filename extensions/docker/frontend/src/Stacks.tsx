/** Reiter „Stacks“: die Compose-Projekte eines Servers, auch fremde, mit Zustand, Verbrauch und der Datei zum Ansehen. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorText } from "../../../_shared/frontend/src/api";
import { deck } from "../../../_shared/frontend/src/deck";
import { formatBytes } from "../../../_shared/frontend/src/format";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Badge, Button, type Tone } from "../../../_shared/frontend/src/ui";

import { BackupsPanel } from "./Backups";
import { hostAction } from "./hostAction";
import { HostSection } from "./Resources";
import { NewStack } from "./NewStack";
import { JSON_HEADERS, StackEditor, withResult } from "./StackEditor";

interface StackContainer { name: string; service: string | null; state: string; status: string }
interface Changes { changed: string[]; added: string[]; removed: string[] }
export interface StackOut {
  project: string;
  state: "running" | "partial" | "stopped";
  running: number;
  problems: number;
  total: number;
  containers: StackContainer[];
  source: "compose" | "portainer" | "unknown";
  working_dir: string | null;
  config_files: string[];
  is_self: boolean;
  changes: Changes | null;
  changed: boolean | null;
  check_error: string | null;
  adopted: boolean;
  adoption_note: string | null;
}
interface AdoptCheck { ok: boolean; reasons: string[]; working_dir: string | null; config_files: string[]; env_files: string[] }
interface Usage { cpu_percent: number | null; mem_used: number | null }
interface FileOut { path: string; content: string | null; error: string | null }
interface EnvOut { path: string; names: string[]; error: string | null; note?: string | null }
type Panel = "file" | "adopt" | "edit" | "delete" | "update" | "portainer" | "backups";

const STATE: Record<StackOut["state"], { label: string; tone: Tone }> = {
  running: { label: "läuft", tone: "good" },
  partial: { label: "läuft teilweise", tone: "warn" },
  stopped: { label: "gestoppt", tone: "neutral" },
};

const SOURCE_NOTE: Record<string, string> = {
  portainer: "Von Portainer verwaltet: Die Datei liegt in Portainers eigenem Speicher, nicht in einem Ordner auf dem Server.",
  unknown: "Docker nennt keine Compose-Datei, die Nodvard Deck lesen kann: sehr altes Compose oder ungewöhnliche Zeichen im Pfad.",
};

const MEMBER_STATE: Record<string, string> = {
  exited: "gestoppt", created: "erstellt", restarting: "startet neu", paused: "pausiert", dead: "tot", removing: "wird entfernt",
};

/** „shop-web-1, shop-worker-1 (gestoppt)“: der Zustand nur, wo er vom Laufen abweicht. */
export function membersText(containers: StackContainer[]): string {
  return containers
    .map((c) => {
      const note = c.status.includes("(unhealthy)") ? "ungesund" : c.state === "running" ? "" : MEMBER_STATE[c.state] ?? c.state;
      return note ? `${c.name} (${note})` : c.name;
    })
    .join(", ");
}

/** „geändert: web · neu in der Datei: cache“; leer, wenn nichts aussteht. */
export function changesText(changes: Changes): string {
  return [
    changes.changed.length ? `geändert: ${changes.changed.join(", ")}` : "",
    changes.added.length ? `neu in der Datei: ${changes.added.join(", ")}` : "",
    changes.removed.length ? `nicht mehr in der Datei: ${changes.removed.join(", ")}` : "",
  ].filter(Boolean).join(" · ");
}

function StackFile({ hostId, project }: { hostId: string; project: string }): JSX.Element {
  const [files, setFiles] = useState<FileOut[] | null>(null);
  const [env, setEnv] = useState<EnvOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setFiles(null);
    setError(null);
    fetchJson<{ files: FileOut[]; env?: EnvOut[] }>(`/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/file`, { signal: controller.signal })
      .then((body) => {
        if (controller.signal.aborted) return;
        setFiles(body.files);
        setEnv(body.env ?? []);
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [hostId, project, reload]);

  if (error) return <LoadError what="Die Compose-Datei" error={error} onRetry={() => setReload((n) => n + 1)} />;
  if (!files) return <p className="text-xs opacity-60">Lade die Datei …</p>;
  return (
    <div className="flex flex-col gap-3" data-testid={`stack-file-${project}`}>
      <p className="text-xs opacity-60">Nur zum Ansehen. So steht die Datei gerade auf dem Server.</p>
      {files.map((file) => (
        <div key={file.path}>
          <p className="mb-1 break-all font-mono text-xs opacity-80">{file.path}</p>
          {file.content != null ? (
            <pre className="max-h-96 overflow-auto rounded bg-black/60 p-2 font-mono text-[11px] leading-snug">{file.content}</pre>
          ) : (
            <p className="text-xs text-red-400">{file.error}</p>
          )}
        </div>
      ))}
      {env.map((file) => (
        <div key={file.path} data-testid="stack-env">
          <p className="mb-1 break-all font-mono text-xs opacity-80">{file.path}</p>
          {file.error ? <p className="text-xs text-red-400">{file.error}</p> : (
            <p className="break-words text-xs">
              {file.names.length ? <>Variablen: <span className="font-mono">{file.names.join(", ")}</span></> : "Keine Variablen."}
            </p>
          )}
          {file.note && <p className="text-xs text-amber-300">{file.note}</p>}
        </div>
      ))}
      {env.length > 0 && <p className="text-xs opacity-60">Die Werte aus .env zeigt Nodvard Deck nicht, weil darin oft Passwörter stehen.</p>}
    </div>
  );
}

const ADOPT_TEXT =
  "Übernehmen heißt: Nodvard Deck merkt sich Ordner und Dateien dieses Stacks. Am Server ändert sich dabei nichts, kein Container wird " +
  "neu erstellt. Vorerst bleibt ein übernommener Stack dadurch in der Liste, auch wenn gerade keiner seiner Container existiert, und " +
  "seine Datei lässt sich weiter ansehen.";

function AdoptPanel({ hostId, project, onDone }: { hostId: string; project: string; onDone: (message: string) => void }): JSX.Element {
  const [check, setCheck] = useState<AdoptCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/adopt`;

  useEffect(() => {
    const controller = new AbortController();
    setCheck(null);
    setError(null);
    fetchJson<AdoptCheck>(url, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setCheck(body); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [url, reload]);

  async function adopt(shown: AdoptCheck) {
    setBusy(true);
    setFailed(null);
    try {
      const res = await authedFetch(url, {
        method: "POST",
        body: JSON.stringify({ working_dir: shown.working_dir, config_files: shown.config_files, env_files: shown.env_files }),
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) throw new Error(await errorText(res));
      onDone(`„${project}“ ist übernommen.`);
    } catch (err) {
      setFailed(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <LoadError what="Die Prüfung" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!check) return <p className="text-xs opacity-60">Prüfe, ob sich der Stack übernehmen lässt …</p>;
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`adopt-${project}`}>
      <p className="opacity-70">{ADOPT_TEXT}</p>
      {check.ok ? (
        <>
          <p className="break-words">
            Alles bereit. Gemerkt werden der Ordner {check.working_dir} und {[...check.config_files, ...check.env_files].join(", ")}.
          </p>
          <div><Button small variant="primary" disabled={busy} onClick={() => void adopt(check)}>{busy ? "…" : "Jetzt übernehmen"}</Button></div>
          {failed && (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-red-400" role="alert">Übernehmen hat nicht geklappt: {failed}</p>
              <Button small onClick={() => { setFailed(null); setReload((n) => n + 1); }}>Prüfung neu laden</Button>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="text-amber-300">So lässt sich der Stack nicht übernehmen:</p>
          <ul className="list-disc pl-5">{check.reasons.map((reason) => <li key={reason} className="break-words">{reason}</li>)}</ul>
        </>
      )}
    </div>
  );
}

const DELETE_TEXT =
  "Löschen fährt den Stack herunter: Container und Netze sind danach weg, und mit ihnen alles, was nur im Container lag. Die Volumes " +
  "bleiben. Nodvard Deck vergisst den Stack; sein Ordner mit der Compose-Datei bleibt, außer du hakst unten das Löschen an.";

function DeletePanel({ hostId, project, onDone }: { hostId: string; project: string; onDone: (message: string, warn?: boolean) => void }): JSX.Element {
  const [check, setCheck] = useState<{ folder: string; folder_refusal: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [withFolder, setWithFolder] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/delete`;

  useEffect(() => {
    const controller = new AbortController();
    setCheck(null);
    setError(null);
    fetchJson<{ folder: string; folder_refusal: string | null }>(url, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setCheck(body); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [url, reload]);

  async function remove(folder: string) {
    const what = withFolder ? ` Auch der Ordner ${folder} mit allen Dateien darin wird gelöscht; ein Zurück gibt es nicht.` : "";
    if (!(await deck().confirmDialog(`Stack „${project}“ löschen?${what} Die Volumes bleiben.`, { confirmLabel: "Stack löschen" }))) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(url, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ remove_folder: withFolder }) });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Zum Löschen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? `Stack „${project}“ gelöscht.`);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result?.output) onDone(`${action.result.output}\n${outcome.text}`, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Ob gelöscht wurde, steht unter „Aktionen“.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <LoadError what="Die Prüfung" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!check) return <p className="text-xs opacity-60">Prüfe, was sich löschen lässt …</p>;
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`delete-${project}`}>
      <p className="opacity-70">{DELETE_TEXT}</p>
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={withFolder} disabled={busy || check.folder_refusal !== null} onChange={(e) => setWithFolder(e.target.checked)} />
        <span className="break-all">Auch den Ordner {check.folder} mit allen Dateien darin löschen</span>
      </label>
      {check.folder_refusal && <p className="opacity-70">{check.folder_refusal}</p>}
      {withFolder && (
        <p className="text-red-400">
          Darin liegen oft die Daten des Stacks, etwa in Unterordnern wie ./daten. Sie sind danach weg, ein Zurück gibt es nicht.
        </p>
      )}
      {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
      <div><Button small variant="danger" disabled={busy} onClick={() => void remove(check.folder)}>{busy ? "…" : "Stack löschen"}</Button></div>
    </div>
  );
}

interface MovePlan { project: string; folder: string; portainer_id: string | null; services: string[]; stopped?: boolean; problem: string | null }

const KEEP_IN_PORTAINER =
  "Lösche den Stack danach nicht in Portainer und rolle ihn dort nicht neu aus: Er hat denselben Projektnamen, Portainer würde die neuen Container entfernen oder mit seiner alten Datei ersetzen.";

function PortainerPanel({ hostId, project, onDone }: { hostId: string; project: string; onDone: (message: string, warn?: boolean) => void }): JSX.Element {
  const [plan, setPlan] = useState<MovePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/from-portainer`;

  useEffect(() => {
    const controller = new AbortController();
    setPlan(null);
    setError(null);
    fetchJson<MovePlan>(url, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setPlan(body); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [url, reload]);

  async function move(folder: string) {
    const started = plan?.stopped ? " Der Stack ist gerade gestoppt; der Umzug startet ihn." : "";
    const question = `Stack „${project}“ von Portainer nach ${folder} holen und dort neu ausrollen? Die Container werden einmal neu erstellt, die Volumes bleiben; `
      + `was nur in den alten Containern lag und nicht in einem Volume oder Ordner, ist danach weg.${started} ${KEEP_IN_PORTAINER}`;
    if (!(await deck().confirmDialog(question, { confirmLabel: "Umziehen" }))) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(url, { method: "POST", headers: JSON_HEADERS, body: "{}" });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Zum Umzug vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? `Stack „${project}“ umgezogen.`);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result?.output) onDone(`${action.result.output}\n${outcome.text}`, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter „Aktionen“.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <LoadError what="Die Prüfung" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!plan) return <p className="text-xs opacity-60">Prüfe, ob der Umzug geht …</p>;
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`portainer-${project}`}>
      <p className="opacity-70">
        Nodvard Deck holt Datei und Variablen dieses Stacks aus Portainer in den Ordner <span className="break-all font-mono">{plan.folder}</span> und
        rollt ihn dort unter demselben Namen neu aus. Die Container werden dabei einmal neu erstellt, die Volumes mit den Daten bleiben; was nur in
        den alten Containern lag und nicht in einem Volume oder Ordner, geht dabei verloren. Danach bearbeitest du ihn hier.
      </p>
      <p className="text-amber-300">{KEEP_IN_PORTAINER} Lass den Eintrag stehen oder schalte Portainer ganz ab, wenn alle Stacks umgezogen sind.</p>
      {plan.stopped && <p className="text-amber-300">Der Stack ist gerade gestoppt; der Umzug startet ihn. Dienste für einmalige Aufgaben laufen dabei noch einmal.</p>}
      {plan.problem && <p className="break-words text-red-400" role="alert">{plan.problem}</p>}
      {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
      <div><Button small variant="primary" disabled={busy || plan.problem !== null} onClick={() => void move(plan.folder)}>{busy ? "…" : "Umziehen"}</Button></div>
    </div>
  );
}

interface UpdatePlan {
  services: {
    service: string; container: string; image: string; status: string | null; reason: string | null; data_store: boolean; backup: boolean;
    stopped?: boolean; mixed?: boolean; backup_at?: string | null; volumes?: string[];
  }[];
  updates: string[];
  data_store: boolean;
  rollback: string[];
  unapplied?: string[];
  unapplied_error?: string | null;
  volume_backup?: { volumes: Record<string, number | null>; free_bytes: number | null; folder: string | null; error: string | null } | null;
}

const IMAGE_STATUS: Record<string, string> = {
  update: "Update verfügbar", current: "aktuell", local: "selbst gebaut, keine Registry", unknown: "nicht prüfbar",
};

/** Die gewählten Dienste und alle, die denselben Image-Namen nutzen: Ein Update verschiebt den Namen für alle zugleich. */
function withSharers(plan: UpdatePlan, services: string[]): string[] {
  const images = new Set(plan.services.filter((s) => services.includes(s.service)).map((s) => s.image));
  return [...services, ...plan.services.filter((s) => !services.includes(s.service) && images.has(s.image)).map((s) => s.service)];
}

function backupDate(plan: UpdatePlan): string | null {
  const dates = plan.services.filter((s) => s.backup && s.backup_at).map((s) => new Date(s.backup_at as string));
  if (dates.length === 0) return null;
  return new Date(Math.max(...dates.map((d) => d.getTime()))).toLocaleDateString("de-DE");
}

function UpdatePanel({ hostId, project, onDone }: { hostId: string; project: string; onDone: (message: string, warn?: boolean) => void }): JSX.Element {
  const [plan, setPlan] = useState<UpdatePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [saveVolumes, setSaveVolumes] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}`;

  useEffect(() => {
    const controller = new AbortController();
    setPlan(null);
    setError(null);
    fetchJson<UpdatePlan>(`${base}/update`, { signal: controller.signal })
      .then((body) => {
        if (controller.signal.aborted) return;
        setPlan(body);
        setChosen(body.updates);
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [base, reload]);

  async function send(path: "update" | "rollback", services: string[], question: string, label: string, backup = false) {
    if (!(await deck().confirmDialog(question, { confirmLabel: label }))) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(`${base}/${path}`, {
        method: "POST", headers: JSON_HEADERS, body: JSON.stringify(path === "update" ? { services, backup } : { services }),
      });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? outcome.text);
      else if (outcome.tone === "pending") onDone(run.text);
      // Gelaufen, aber nicht sauber: Stack und Übersicht haben sich geändert, also neu laden und als Warnung zeigen.
      else if (action.result) onDone(action.result.error ?? outcome.text, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter „Aktionen“.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <LoadError what="Die Übersicht" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!plan) return <p className="text-xs opacity-60">Lade die Übersicht …</p>;
  const together = withSharers(plan, chosen);
  const sharers = together.filter((s) => !chosen.includes(s));
  const stored = plan.services.filter((s) => together.includes(s.service) && s.data_store).map((s) => s.service);
  const unapplied = plan.unapplied ?? [];
  const blockedUpdate = together.filter((s) => unapplied.includes(s));
  const blockedBack = plan.rollback.filter((s) => unapplied.includes(s));
  const checkError = plan.unapplied_error ?? null;
  const since = backupDate(plan);
  const pause = "Die gewählten Dienste werden neu erstellt und sind dabei kurz nicht erreichbar.";
  const volumePlan = plan.volume_backup;
  const volumes = [...new Set(plan.services.filter((s) => together.includes(s.service)).flatMap((s) => s.volumes ?? []))].sort();
  const sizes = volumes.map((v) => volumePlan?.volumes[v]);
  const size = sizes.every((v) => v != null) ? formatBytes(sizes.reduce<number>((sum, v) => sum + (v ?? 0), 0)) : "Größe unbekannt";
  const canSave = !!volumePlan && !volumePlan.error && volumes.length > 0;
  const withBackup = saveVolumes && canSave;
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`update-${project}`}>
      <p className="opacity-70">
        Das Update holt die neuen Images, behält die alten als Sicherung und erstellt nur die gewählten Dienste neu. Startet einer danach nicht,
        geht es von selbst zur alten Version zurück, außer bei Datenbanken und anderen Datenspeichern.
      </p>
      <ul className="flex flex-col gap-1">
        {plan.services.map((s) => (
          <li key={s.service} className="flex flex-wrap items-center gap-2">
            {s.status === "update" && !s.stopped && !s.mixed ? (
              <input type="checkbox" aria-label={`${s.service} aktualisieren`} checked={chosen.includes(s.service)} disabled={busy}
                onChange={(e) => setChosen((all) => (e.target.checked ? [...all, s.service] : all.filter((x) => x !== s.service)))} />
            ) : <span className="w-[13px]" />}
            <span className="font-mono">{s.service}</span>
            <span className="break-all opacity-60">{s.image}</span>
            <Badge tone={s.status === "update" ? "warn" : "neutral"}>{IMAGE_STATUS[s.status ?? ""] ?? "noch nicht geprüft"}</Badge>
            {s.data_store && <Badge tone="info">Datenspeicher</Badge>}
            {s.stopped && <Badge tone="neutral">gestoppt</Badge>}
            {s.mixed && <Badge tone="warn">Container mit verschiedenen Images</Badge>}
            {s.reason && <span className="opacity-60">{s.reason}</span>}
          </li>
        ))}
      </ul>
      {plan.updates.length === 0 && <p className="opacity-70">Für keinen Dienst meldet die Image-Prüfung gerade ein Update.</p>}
      {plan.services.some((s) => s.stopped && s.status === "update") && (
        <p className="opacity-70">Gestoppte Dienste aktualisiert Deck hier nicht, weil sie dabei starten würden; sie bekommen das Update beim nächsten Anwenden.</p>
      )}
      {sharers.length > 0 && (
        <p className="opacity-70">{sharers.join(", ")} {sharers.length === 1 ? "nutzt" : "nutzen"} dasselbe Image und {sharers.length === 1 ? "kommt" : "kommen"} mit.</p>
      )}
      {stored.length > 0 && (
        <p className="text-amber-300">
          Dabei ist ein Datenspeicher ({stored.join(", ")}): Geht etwas schief, springt Deck nicht von selbst zurück, und die Freigabe braucht die hohe Stufe.
          Lege vorher eine Sicherung der Daten an.
        </p>
      )}
      {(blockedUpdate.length > 0 || blockedBack.length > 0) && (
        <p className="text-amber-300" role="alert">
          In der Datei steht für {[...new Set([...blockedUpdate, ...blockedBack])].join(", ")} etwas anderes, als gerade läuft. Ein Update oder der Rückweg
          würde das mit anwenden. Wende den Stack zuerst an oder nimm die Änderung zurück.
        </p>
      )}
      {plan.updates.length > 0 && volumes.length > 0 && (
        <div className="flex flex-col gap-1">
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={withBackup} disabled={busy || !canSave} onChange={(e) => setSaveVolumes(e.target.checked)} />
            <span className="break-all">
              Volumes vorher sichern ({volumes.join(", ")}: {size}{volumePlan?.free_bytes != null ? `, frei ${formatBytes(volumePlan.free_bytes)}` : ""})
            </span>
          </label>
          {volumePlan?.error ? <p className="opacity-70">{volumePlan.error}</p> : (
            <p className="opacity-70">
              Die Dienste sind während der Sicherung gestoppt. Die Archive kommen nach {volumePlan?.folder ?? "…"}; nur sie bringen auch Daten zurück,
              die die neue Version umbaut.
            </p>
          )}
        </div>
      )}
      {checkError && <p className="text-amber-300" role="alert">{checkError}</p>}
      {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
      <div className="flex flex-wrap gap-1.5">
        {plan.updates.length > 0 && (
          <Button small variant="primary" disabled={busy || chosen.length === 0 || blockedUpdate.length > 0 || checkError !== null}
            onClick={() => void send("update", chosen, `Stack „${project}“ aktualisieren (${together.join(", ")})? ${pause}${
              withBackup ? " Vorher werden die Volumes gesichert; die Dienste sind dabei gestoppt." : ""}`, "Aktualisieren", withBackup)}>
            {busy ? "…" : "Jetzt aktualisieren"}
          </Button>
        )}
        {plan.rollback.length > 0 && (
          <Button small disabled={busy || blockedBack.length > 0 || checkError !== null}
            onClick={() => void send("rollback", plan.rollback, `${plan.rollback.join(", ")} von „${project}“ auf die Version vor dem letzten Update zurücksetzen? ${pause} Daten, die die neue Version schon umgebaut hat, bleiben so.`, "Zurücksetzen")}>
            Zurück zur vorigen Version ({plan.rollback.join(", ")}{since ? `, Sicherung vom ${since}` : ""})
          </Button>
        )}
      </div>
    </div>
  );
}

/** Die Stack-Aktionen über das Gate; nur für übernommene Stacks. */
const VERBS: { type: string; label: string; question: (project: string) => string; confirm: string; when: (s: StackOut) => boolean }[] = [
  {
    type: "docker.stack_up", label: "Anwenden", question: (p) => `Stack „${p}“ anwenden?`,
    confirm: "Der Stack startet nach seiner Compose-Datei. Geänderte Dienste werden dabei neu erstellt und sind kurz nicht erreichbar.",
    when: () => true,
  },
  {
    type: "docker.stack_stop", label: "Stoppen", question: (p) => `Stack „${p}“ stoppen?`,
    confirm: "Alle Container des Stacks werden gestoppt. Seine Dienste sind nicht erreichbar, bis du ihn wieder anwendest.",
    when: (s) => s.running > 0,
  },
  {
    type: "docker.stack_down", label: "Herunterfahren", question: (p) => `Stack „${p}“ herunterfahren?`,
    confirm: "Container und Netze des Stacks werden gelöscht. Was nur im Container lag und nicht in einem Volume oder Ordner, ist danach weg. Die Volumes bleiben, und „Anwenden“ legt die Container neu an.",
    when: (s) => s.total > 0,
  },
  {
    type: "docker.stack_pull", label: "Images holen", question: (p) => `Images für den Stack „${p}“ neu holen?`,
    confirm: "Die Images des Stacks werden neu heruntergeladen. Die Container laufen dabei weiter; neu erstellt werden sie erst beim nächsten „Anwenden“.",
    when: () => true,
  },
];

function usageText(usage: Usage | undefined, measured: boolean, running: number): string {
  if (!running) return "";
  if (!measured) return "CPU/RAM werden gemessen …";
  if (!usage) return "CPU/RAM unbekannt";
  const cpu = usage.cpu_percent != null ? `${usage.cpu_percent.toFixed(1)} %` : "n.\u00a0v.";
  return `CPU ${cpu} · RAM ${usage.mem_used != null ? formatBytes(usage.mem_used) : "n.\u00a0v."}`;
}

export function StackList({ hostId }: { hostId: string }): JSX.Element {
  const [stacks, setStacks] = useState<StackOut[] | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [usage, setUsage] = useState<Record<string, Usage>>({});
  const [measured, setMeasured] = useState(false);
  const [open, setOpen] = useState<{ project: string; panel: Panel } | null>(null);
  const [message, setMessage] = useState<{ text: string; warn: boolean } | null>(null);
  const note = (text: string | null, warn = false) => setMessage(text === null ? null : { text, warn });
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks`;
    setStacks(null);
    setError(null);
    setMeasured(false);
    fetchJson<{ stacks: StackOut[]; check_error: string | null }>(base, { signal: controller.signal })
      .then((body) => {
        if (controller.signal.aborted) return;
        setStacks(body.stacks);
        setCheckError(body.check_error);
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    fetchJson<{ usage: Record<string, Usage> }>(`${base}/usage`, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setUsage(body.usage); })
      .catch(() => { if (!controller.signal.aborted) setUsage({}); })
      .finally(() => { if (!controller.signal.aborted) setMeasured(true); });
    return () => controller.abort();
  }, [hostId, reload]);

  const editorDirty = useRef(false);
  const markDirty = useCallback((dirty: boolean) => { editorDirty.current = dirty; }, []);

  async function toggle(project: string, panel: Panel) {
    if (open?.panel === "edit" && editorDirty.current
      && !(await deck().confirmDialog("Deine Änderung im Editor ist noch nicht gespeichert. Verwerfen?", { confirmLabel: "Verwerfen" }))) return;
    editorDirty.current = false;
    setOpen(open?.project === project && open.panel === panel ? null : { project, panel });
  }

  async function act(stack: StackOut, verb: (typeof VERBS)[number]) {
    const ok = await deck().confirmDialog(`${verb.question(stack.project)} ${verb.confirm}`, { confirmLabel: verb.label });
    if (!ok) return;
    setBusy(`${stack.project}:${verb.type}`);
    note(null);
    try {
      const result = await hostAction(hostId, verb.type, `Stack „${stack.project}“ über die Docker-Seite: ${verb.label}.`, undefined, { project: stack.project });
      const named = result.text.startsWith(`Stack „${stack.project}“`);
      note(named ? result.text : `Stack „${stack.project}“, ${verb.label}: ${result.text}`);
      setReload((n) => n + 1);
    } catch (err) {
      note(`Fehler: ${errorMessage(err)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function release(stack: StackOut) {
    const project = stack.project;
    const ok = await deck().confirmDialog(
      `Übernahme von „${project}“ aufheben? Nodvard Deck vergisst nur Ordner und Dateien; Stack, Datei und Container bleiben, wie sie sind.${
        stack.total ? "" : " Weil gerade keiner seiner Container existiert, verschwindet er danach aus der Liste."}`,
      { confirmLabel: "Übernahme aufheben" },
    );
    if (!ok) return;
    note(null);
    try {
      const res = await authedFetch(`/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/adopt`, { method: "DELETE" });
      if (!res.ok) throw new Error(await errorText(res));
      note(`Übernahme von „${project}“ aufgehoben.`);
      setReload((n) => n + 1);
    } catch (err) {
      note(`Fehler: ${errorMessage(err)}`, true);
    }
  }

  if (error) return <LoadError what="Die Liste der Stacks" error={error} onRetry={() => setReload((n) => n + 1)} />;
  if (!stacks) return <p className="text-sm opacity-60">Lade die Stacks …</p>;
  return (
    <div data-testid="stacks">
      <p className="mb-2 text-xs opacity-60">
        Ein Stack ist ein Compose-Projekt: Container, die zusammen aus einer Compose-Datei gestartet wurden. Hier stehen alle, auch die von Hand oder mit
        anderen Werkzeugen angelegten. Maßgeblich ist immer die Datei auf dem Server.
      </p>
      {message && (
        <p className={`mb-2 whitespace-pre-line text-sm ${message.warn ? "text-red-400" : "opacity-90"}`} role={message.warn ? "alert" : "status"}>
          {message.text}
        </p>
      )}
      {creating ? (
        <div className="mb-3 bg-black/20 p-2 panel">
          <NewStack hostId={hostId} onClose={(text) => { setCreating(false); if (text) { note(text); setReload((n) => n + 1); } }} />
        </div>
      ) : (
        <div className="mb-2"><Button small onClick={() => setCreating(true)}>Neuer Stack</Button></div>
      )}
      {checkError && <p className="mb-2 text-xs text-amber-300">Ob Dateien seit dem letzten Start geändert wurden, ließ sich nicht prüfen: {checkError}</p>}
      {stacks.length === 0 && <p className="text-sm opacity-70">Auf diesem Server läuft kein Compose-Projekt.</p>}
      <ul className="divide-y divide-white/5 text-sm">
        {stacks.map((stack) => {
          const state = STATE[stack.state];
          const pending = stack.changes ? changesText(stack.changes) : "";
          const facts: string[] = [
            stack.total ? `${stack.running} von ${stack.total} Containern laufen` : "gerade keine Container",
            ...(stack.working_dir && stack.source === "compose" ? [stack.working_dir] : []),
            usageText(usage[stack.project], measured, stack.running),
          ].filter(Boolean);
          return (
            <li key={stack.project} className="py-2" data-testid={`stack-${stack.project}`}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <div className="min-w-0 flex-1 max-md:basis-full">
                  <p className="flex flex-wrap items-center gap-1.5 break-words">
                    <span className="font-medium">{stack.project}</span>
                    <Badge tone={state.tone}>{state.label}</Badge>
                    {stack.problems > 0 && <Badge tone="bad">{stack.problems === 1 ? "1 Container mit Problemen" : `${stack.problems} Container mit Problemen`}</Badge>}
                    {stack.is_self && <Badge>Nodvard Deck</Badge>}
                    {stack.source === "portainer" && <Badge tone="info">Portainer</Badge>}
                    {stack.adopted && <Badge tone="accent">übernommen</Badge>}
                    {stack.changed && <Badge tone="warn">Datei geändert, noch nicht angewendet</Badge>}
                  </p>
                  <p className="break-words text-xs opacity-60">{facts.join(" · ")}</p>
                  {stack.containers.length > 0 && <p className="break-words text-xs opacity-60">Container: {membersText(stack.containers)}</p>}
                  {stack.changed && (
                    <p className="text-xs text-amber-300">
                      {pending}. Übernommen wird das erst, wenn der Stack neu angewendet wird (docker compose up -d). Kommt das
                      unerwartet, wurde der Stack vielleicht mit eigenen Umgebungsvariablen gestartet.
                    </p>
                  )}
                  {stack.check_error && <p className="text-xs opacity-70">{stack.check_error}</p>}
                  {SOURCE_NOTE[stack.source] && <p className="text-xs opacity-70">{SOURCE_NOTE[stack.source]}</p>}
                  {stack.adoption_note && <p className="text-xs text-amber-300">{stack.adoption_note}</p>}
                </div>
                <div className="flex shrink-0 flex-wrap gap-1.5">
                  {stack.source === "compose" && (
                    <Button small pressed={open?.project === stack.project && open.panel === "file"} onClick={() => void toggle(stack.project, "file")}>
                      Datei ansehen
                    </Button>
                  )}
                  {stack.adopted && !stack.is_self && (
                    <Button small pressed={open?.project === stack.project && open.panel === "edit"} onClick={() => void toggle(stack.project, "edit")}>
                      Bearbeiten
                    </Button>
                  )}
                  {stack.source === "compose" && !stack.adopted && stack.total > 0 && (
                    <Button small pressed={open?.project === stack.project && open.panel === "adopt"} onClick={() => void toggle(stack.project, "adopt")}>
                      {stack.adoption_note ? "Neu übernehmen" : "Übernehmen"}
                    </Button>
                  )}
                  {stack.source === "portainer" && stack.total > 0 && !stack.is_self && (
                    <Button small pressed={open?.project === stack.project && open.panel === "portainer"} onClick={() => void toggle(stack.project, "portainer")}>
                      Von Portainer übernehmen
                    </Button>
                  )}
                  {stack.adopted && VERBS.filter((verb) => verb.when(stack)).map((verb) => (
                    <Button key={verb.type} small disabled={busy !== null} onClick={() => void act(stack, verb)}
                      ariaLabel={`${verb.label}: ${stack.project}`}>
                      {busy === `${stack.project}:${verb.type}` ? "…" : verb.label}
                    </Button>
                  ))}
                  {(stack.adopted || stack.adoption_note) && (
                    <Button small onClick={() => void release(stack)}>Übernahme aufheben</Button>
                  )}
                  {stack.adopted && !stack.is_self && stack.total > 0 && (
                    <Button small pressed={open?.project === stack.project && open.panel === "update"} onClick={() => void toggle(stack.project, "update")}>
                      Update
                    </Button>
                  )}
                  {stack.adopted && !stack.is_self && (
                    <Button small pressed={open?.project === stack.project && open.panel === "backups"} onClick={() => void toggle(stack.project, "backups")}>
                      Sicherungen
                    </Button>
                  )}
                  {stack.adopted && !stack.is_self && (
                    <Button small variant="danger" pressed={open?.project === stack.project && open.panel === "delete"}
                      onClick={() => void toggle(stack.project, "delete")}>
                      Löschen
                    </Button>
                  )}
                </div>
              </div>
              {open?.project === stack.project && (
                <div className="mt-2 bg-black/20 p-2 panel">
                  {open.panel === "file" ? (
                    <StackFile hostId={hostId} project={stack.project} />
                  ) : open.panel === "edit" ? (
                    <StackEditor hostId={hostId} project={stack.project} onDirty={markDirty}
                      onClose={(text) => { editorDirty.current = false; setOpen(null); if (text) { note(text); setReload((n) => n + 1); } }} />
                  ) : open.panel === "update" ? (
                    <UpdatePanel hostId={hostId} project={stack.project} onDone={(text, warn) => { setOpen(null); note(text, warn); setReload((n) => n + 1); }} />
                  ) : open.panel === "portainer" ? (
                    <PortainerPanel hostId={hostId} project={stack.project} onDone={(text, warn) => { setOpen(null); note(text, warn); setReload((n) => n + 1); }} />
                  ) : open.panel === "backups" ? (
                    <BackupsPanel hostId={hostId} project={stack.project} onDone={(text, warn) => { setOpen(null); note(text, warn); setReload((n) => n + 1); }} />
                  ) : open.panel === "delete" ? (
                    <DeletePanel hostId={hostId} project={stack.project} onDone={(text, warn) => { setOpen(null); note(text, warn); setReload((n) => n + 1); }} />
                  ) : (
                    <AdoptPanel hostId={hostId} project={stack.project} onDone={(text) => { setOpen(null); note(text); setReload((n) => n + 1); }} />
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function StacksSection({ hostId, onHost, empty }: { hostId: string | null; onHost: (id: string) => void; empty: ReactNode }): JSX.Element {
  return (
    <HostSection hostId={hostId} onHost={onHost} empty={empty}>
      {(id) => <StackList key={id} hostId={id} />}
    </HostSection>
  );
}
