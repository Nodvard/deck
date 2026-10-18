/** Einen ohne Compose gestarteten Container in einen Stack umwandeln: Vorschau der Datei, Gründe dagegen, dann über die Freigabe. */
import { useEffect, useState } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { deck } from "../../../_shared/frontend/src/deck";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Button } from "../../../_shared/frontend/src/ui";

import { JSON_HEADERS, withResult } from "./StackEditor";

interface ToStackPlan {
  container: string;
  id: string | null;
  project: string | null;
  folder: string | null;
  compose: string | null;
  env_names: string[];
  refused: string[];
  warnings: string[];
}

export function ToStackPanel({ hostId, container, onDone }: { hostId: string; container: string; onDone: (message: string, warn?: boolean) => void }): JSX.Element {
  const [plan, setPlan] = useState<ToStackPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/containers/${encodeURIComponent(container)}/to-stack`;

  useEffect(() => {
    const controller = new AbortController();
    setPlan(null);
    setError(null);
    fetchJson<ToStackPlan>(url, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setPlan(body); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [url, reload]);

  async function convert(folder: string) {
    const risky = plan?.warnings.length ? ` Riskant: ${plan.warnings.join(" ")}` : "";
    const question = `„${container}“ in einen Stack in ${folder} umwandeln? Der Container wird gestoppt und aus der neuen Compose-Datei neu erstellt; `
      + `Volumes und Ordner bleiben. Klappt das, löscht Deck den alten Container; was nur in ihm lag und nicht in einem Volume oder Ordner, ist `
      + `dann weg. Klappt es nicht, startet der alte wieder.${risky}`;
    if (!(await deck().confirmDialog(question, { confirmLabel: "Umwandeln" }))) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(url, { method: "POST", headers: JSON_HEADERS, body: "{}" });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Zum Umwandeln vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? `„${container}“ ist jetzt ein Stack.`);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result) onDone([action.result.output, action.result.error ?? outcome.text].filter(Boolean).join("\n"), true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter „Aktionen“.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <LoadError what="Die Vorschau" error={error} onRetry={() => setReload((n) => n + 1)} compact />;
  if (!plan) return <p className="text-xs opacity-60">Lese den Container …</p>;
  const blocked = plan.refused.length > 0 || !plan.folder;
  return (
    <div className="mt-2 flex flex-col gap-2 text-xs" data-testid={`to-stack-${container}`}>
      <p className="opacity-70">
        Deck schreibt für diesen Container eine Compose-Datei{plan.folder ? <> nach <span className="break-all font-mono">{plan.folder}</span></> : null},
        stoppt ihn und startet ihn daraus neu. Volumes und Ordner bleiben dieselben; danach lässt er sich wie jeder Stack bearbeiten und aktualisieren.
        Den alten Container löscht Deck, sobald der neue läuft; was nur in ihm lag und nicht in einem Volume oder Ordner, geht dabei verloren.
      </p>
      {plan.compose && (
        <pre className="max-h-64 overflow-auto rounded bg-black/60 p-2 font-mono text-[11px] leading-snug" aria-label="Compose-Datei des neuen Stacks">{plan.compose}</pre>
      )}
      {plan.env_names.length > 0 && (
        <p className="opacity-70">Die Umgebungsvariablen ({plan.env_names.join(", ")}) kommen in die Datei container.env daneben, nur für den Zugang lesbar.
          Startbefehl, Labels und Healthcheck stehen dagegen so in der Compose-Datei oben; auch sie ist auf dem Server nur für den Zugang lesbar.</p>
      )}
      {plan.warnings.length > 0 && <p className="text-amber-300">Riskant: {plan.warnings.join(" ")} Die Freigabe braucht die hohe Stufe.</p>}
      {plan.refused.length > 0 && (
        <ul className="list-disc pl-4 text-red-400" role="alert">
          {plan.refused.map((why) => <li key={why} className="break-words">{why}</li>)}
        </ul>
      )}
      {failed && <p className="break-words text-red-400" role="alert">{failed}</p>}
      <div><Button small variant="primary" disabled={busy || blocked} onClick={() => void convert(plan.folder!)}>{busy ? "…" : "Umwandeln"}</Button></div>
    </div>
  );
}
