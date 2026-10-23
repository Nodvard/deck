/** Volume-Sicherungen eines Stacks: Liste, Zurückspielen und Löschen, beides nur über die Freigabe. */
import { useEffect, useState } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { deck } from "../../../_shared/frontend/src/deck";
import { formatBytes } from "../../../_shared/frontend/src/format";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Badge, Button } from "../../../_shared/frontend/src/ui";

import { JSON_HEADERS, withResult } from "./StackEditor";

interface BackupVolume { name: string; size_bytes: number | null; services: string[]; stopped_services?: string[]; restorable: boolean }
interface Backup {
  stamp: string; at: string; folder: string; volumes: BackupVolume[]; unfinished?: string[]; missing?: string[]; contents_error?: string | null;
}
interface BackupList { backups: Backup[]; folder: string | null; error: string | null; note?: string | null; busy?: string | null }

const WAITING = "Vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.";

function named(names: string[], one: string, many: string): string {
  return `${names.join(", ")} ${names.length === 1 ? one : many}`;
}

/** Was in die Sicherung gehört, aber nicht als fertiges Archiv darin liegt. */
function notContained(backup: Backup): string[] {
  return [...new Set([...(backup.missing ?? []), ...(backup.unfinished ?? [])])].sort();
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString("de-DE", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function BackupsPanel({ hostId, project, onDone }: { hostId: string; project: string; onDone: (message: string, warn?: boolean) => void }): JSX.Element {
  const [list, setList] = useState<BackupList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/backups`;

  useEffect(() => {
    const controller = new AbortController();
    setList(null);
    setError(null);
    fetchJson<BackupList>(base, { signal: controller.signal })
      .then((body) => {
        if (controller.signal.aborted) return;
        setList(body);
        setChosen(Object.fromEntries(body.backups.map((b) => [b.stamp, b.volumes.filter((v) => v.restorable).map((v) => v.name)])));
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [base, reload]);

  async function send(path: string, body: object, question: string, label: string, danger: boolean) {
    if (!(await deck().confirmDialog(question, { confirmLabel: label, danger }))) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(`${base}/${path}`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone(WAITING);
      else if (outcome.tone === "success") onDone(action.result?.output ?? outcome.text);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result) onDone(action.result.error ?? outcome.text, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter „Aktionen“.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function restore(backup: Backup) {
    const volumes = chosen[backup.stamp] ?? [];
    const picked = backup.volumes.filter((v) => volumes.includes(v.name));
    // Deck startet nur wieder, was lief; Compose startet aber Abhängigkeiten eines laufenden Dienstes mit.
    const idle = [...new Set(picked.flatMap((v) => v.stopped_services ?? []))].sort();
    const services = [...new Set(picked.flatMap((v) => v.services))].filter((s) => !idle.includes(s)).sort();
    let question = `${volumes.join(", ")} auf den Stand vom ${when(backup.at)} zurückspielen? Was seitdem in ${volumes.length === 1 ? "diesem Volume" : "diesen Volumes"} `
      + "dazukam oder sich änderte, ist danach weg.";
    question += services.length > 0
      ? ` ${named(services, "stoppt", "stoppen")} dafür, bis das Zurückspielen fertig ist; bei großen Volumes kann das dauern.`
      : " Bei großen Volumes kann das dauern.";
    if (idle.length > 0 && services.length === 0) question += ` ${named(idle, "ist gestoppt und bleibt", "sind gestoppt und bleiben")} es.`;
    else if (idle.length > 0) {
      question += idle.length === 1
        ? ` ${idle[0]} ist gestoppt. Deck startet ihn nicht; Compose kann ihn aber mitstarten, wenn ein laufender Dienst ihn braucht, etwa als Einmal-Dienst für Migrationen.`
        : ` ${idle.join(", ")} sind gestoppt. Deck startet sie nicht; Compose kann sie aber mitstarten, wenn ein laufender Dienst sie braucht, etwa als Einmal-Dienst für Migrationen.`;
    }
    const gaps = notContained(backup);
    if (gaps.length > 0) {
      question += ` ${named(gaps, "ist nicht in dieser Sicherung und bleibt", "sind nicht in dieser Sicherung und bleiben")} auf dem heutigen Stand; `
        + "die Daten der Dienste passen danach vielleicht nicht zusammen.";
    }
    void send(`${encodeURIComponent(backup.stamp)}/restore`, { volumes }, question, "Zurückspielen", true);
  }

  function drop(backup: Backup) {
    const question = `Sicherung vom ${when(backup.at)} löschen? Sie lässt sich danach nicht mehr zurückspielen.`;
    void send(`${encodeURIComponent(backup.stamp)}/delete`, {}, question, "Löschen", true);
  }

  if (error) return <LoadError what="Die Sicherungen" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!list) return <p className="text-xs opacity-60">Lade die Sicherungen …</p>;
  if (list.error) return <LoadError what="Die Sicherungen" error={list.error} onRetry={() => setReload((n) => n + 1)} compact />;
  const canChange = deck().hasPermission("hosts.execute");
  return (
    <div className="flex flex-col gap-2 text-xs" data-testid={`backups-${project}`}>
      <p className="opacity-70">
        Sicherungen entstehen beim Update mit dem Haken „Volumes vorher sichern“ und liegen in <span className="break-all font-mono">{list.folder ?? "…"}</span>.
        Zurückspielen ersetzt die Daten im Volume durch den Stand der Sicherung. Deck prüft vorher das Archiv und den freien Platz; ist etwas
        nicht in Ordnung, bleiben die Daten, wie sie sind.
      </p>
      {list.note && <p className="text-amber-300" role="status">{list.note}</p>}
      {list.busy && (
        <div className="flex flex-wrap items-center gap-2 text-amber-300" role="status">
          <span>Am Stack läuft gerade etwas ({list.busy}). Zurückspielen und Löschen gehen erst danach.</span>
          <Button small onClick={() => setReload((n) => n + 1)}>Neu laden</Button>
        </div>
      )}
      {list.backups.length === 0 && <p className="opacity-70">Für diesen Stack gibt es noch keine Sicherung.</p>}
      <ul className="flex flex-col gap-2">
        {list.backups.map((backup) => {
          const picked = chosen[backup.stamp] ?? [];
          const unfinished = backup.unfinished ?? [];
          const gaps = notContained(backup);
          return (
            <li key={backup.stamp} className="flex flex-col gap-1 rounded border border-white/10 p-2" data-testid={`backup-${backup.stamp}`}>
              <p className="font-medium">{when(backup.at)}</p>
              <ul className="flex flex-col gap-0.5">
                {backup.volumes.map((v) => (
                  <li key={v.name} className="flex flex-wrap items-center gap-2">
                    {v.restorable ? (
                      <input type="checkbox" aria-label={`${v.name} zurückspielen`} checked={picked.includes(v.name)} disabled={busy}
                        onChange={(e) => setChosen((all) => ({
                          ...all, [backup.stamp]: e.target.checked ? [...picked, v.name] : picked.filter((x) => x !== v.name),
                        }))} />
                    ) : <span className="w-[13px]" />}
                    <span className="font-mono">{v.name}</span>
                    <span className="opacity-60">{v.size_bytes != null ? formatBytes(v.size_bytes) : "Größe unbekannt"}</span>
                    {v.restorable ? (
                      <span className="opacity-60">
                        {v.services.map((s) => (v.stopped_services?.includes(s) ? `${s} (gestoppt)` : s)).join(", ")}
                      </span>
                    ) : <Badge>nutzt der Stack nicht mehr</Badge>}
                  </li>
                ))}
              </ul>
              {backup.volumes.length === 0 && <p className="opacity-70">Diese Sicherung enthält keine Archive.</p>}
              {gaps.length > 0 && (list.busy ? (
                <p className="opacity-70">Noch nicht fertig: {gaps.join(", ")}. Vielleicht packt Deck gerade daran.</p>
              ) : (
                <p className="text-amber-300">
                  Nicht enthalten: {gaps.join(", ")}. Die Sicherung ist unvollständig, etwa weil das Packen abbrach; spielst du nur einen Teil
                  zurück, passen die Daten der Dienste vielleicht nicht zusammen.
                  {unfinished.length > 0 && ` Von ${unfinished.join(", ")} liegt noch ein halbes Archiv im Ordner; „Sicherung löschen“ nimmt die Reste mit.`}
                </p>
              ))}
              {backup.contents_error && <p className="text-amber-300">{backup.contents_error}</p>}
              {canChange && (
                <div className="flex flex-wrap gap-1.5">
                  <Button small variant="danger" disabled={busy || Boolean(list.busy) || picked.length === 0} onClick={() => restore(backup)}>
                    {busy ? "…" : "Zurückspielen"}
                  </Button>
                  <Button small disabled={busy || Boolean(list.busy)} onClick={() => drop(backup)}>Sicherung löschen</Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
    </div>
  );
}
