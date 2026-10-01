/**
 * "Alle Updates vorschlagen (N)": ein Knopf je Docker-Host, sobald mindestens zwei Container
 * dort ein Image-Update einspielbar haben (dieselben Bedingungen wie der Knopf "Einspielen"
 * in der Zeile, siehe `ImageUpdateAction`).
 *
 * Ablauf: fuer jeden Container erst die Uebersicht holen (`GET .../image-update/plan`, nur
 * lesend), dann EINE Bestaetigung mit allen Containern (und der Datenbank-Warnung, falls einer
 * dabei ist), dann je Container `POST .../image-update` mit der `plan_id` seiner Uebersicht.
 * Bewusst OHNE Selbst-Freigabe: das sind Vorschlaege; freigegeben wird gesammelt unter
 * "Aktionen" (Sammel-Freigabe). Darum wird hier `authedFetch` direkt benutzt und nie
 * `settleAction` oder `/actions/<id>/approve`.
 *
 * Alle Anfragen laufen nacheinander -- SSH zum Host und die Registries sollen nicht
 * gleichzeitig beschossen werden.
 */
import { useState } from "react";

import { authedFetch, errorFromBody, errorText } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { deck } from "../../../_shared/frontend/src/deck";

import type { UpdatePlan } from "./ImageUpdateApply";

export interface BulkCandidate {
  hostId: string;
  container: string;
  name: string;
}

interface Skipped {
  name: string;
  reason: string;
}
interface Result {
  created: number;
  skipped: Skipped[];
}

const ACTIONS_PATH = "/actions";
const BUTTON = "px-2 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";

const isDatabaseWarning = (w: string) => w.startsWith("Datenbank");

function endpoint(c: BulkCandidate): string {
  return `/ext/service-matrix/containers/${c.hostId}/${encodeURIComponent(c.container)}/image-update`;
}

export function BulkProposeButton({
  hostId, hostName, candidates, onDone,
}: {
  hostId: string;
  hostName: string;
  /** Die Container dieses Hosts, die hier einspielbar sind (Filterung macht die Seite). */
  candidates: BulkCandidate[];
  /** Nach dem Lauf: Stand der Image-Updates neu laden. */
  onDone: () => void;
}): JSX.Element | null {
  const unmountSignal = useUnmountSignal();
  const [progress, setProgress] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const n = candidates.length;

  async function run() {
    setResult(null);
    setError(null);
    const signal = unmountSignal();
    const ready: { candidate: BulkCandidate; plan: UpdatePlan }[] = [];
    const skipped: Skipped[] = [];
    try {
      // 1. Uebersichten holen (nur lesend).
      for (const [i, c] of candidates.entries()) {
        if (signal.aborted) return;
        setProgress(`Prüfe ${i + 1}/${n} …`);
        try {
          const res = await authedFetch(`${endpoint(c)}/plan`, { signal });
          if (!res.ok) throw new Error(await errorText(res));
          const plan = (await res.json()) as UpdatePlan | { ok: false; reason: string };
          if (plan.ok) ready.push({ candidate: c, plan });
          else skipped.push({ name: c.name, reason: plan.reason });
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          skipped.push({ name: c.name, reason: err instanceof Error ? err.message : String(err) });
        }
      }

      // 2. Eine Bestaetigung fuer alle.
      if (ready.length > 0) {
        const databases = ready.filter((r) => r.plan.risk === "high" || r.plan.warnings.some(isDatabaseWarning)).map((r) => r.candidate.name);
        const parts = [
          `Für ${ready.length} Container auf „${hostName}“ Update-Vorschläge anlegen: ${ready.map((r) => r.candidate.name).join(", ")}.`,
          "Es wird noch nichts eingespielt – freigegeben wird gesammelt unter „Aktionen“; beim Einspielen sind die Container kurz nicht erreichbar.",
        ];
        if (databases.length > 0) {
          parts.push(`Achtung Datenbank (${databases.join(", ")}): ein neues Image kann die Datenbank-Dateien auf eine neue Version umstellen – vorher ein Backup machen.`);
        }
        if (skipped.length > 0) parts.push(`Übersprungen: ${skipped.map((s) => `${s.name} (${s.reason})`).join("; ")}.`);
        const ok = await deck().confirmDialog(parts.join(" "), { confirmLabel: "Vorschläge anlegen", danger: databases.length > 0 });
        if (!ok || signal.aborted) return;
      }

      // 3. Vorschlaege anlegen, einer nach dem anderen.
      let created = 0;
      for (const [i, { candidate, plan }] of ready.entries()) {
        if (signal.aborted) return;
        setProgress(`Lege Vorschlag ${i + 1}/${ready.length} an …`);
        try {
          const res = await authedFetch(endpoint(candidate), { method: "POST", body: JSON.stringify({ plan_id: plan.plan_id }), signal });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(errorFromBody(body, res.status));
          created += 1;
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          skipped.push({ name: candidate.name, reason: err instanceof Error ? err.message : String(err) });
        }
      }
      setResult({ created, skipped });
      onDone();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setProgress(null);
    }
  }

  if (n < 2 && !result && !error && !progress) return null;
  return (
    <>
      {(n >= 2 || progress) && (
        <button type="button" disabled={progress !== null} onClick={() => void run()}
          title="Legt für jeden Container mit neuer Version einen Vorschlag an. Es wird nichts eingespielt, bevor ein Admin unter „Aktionen“ freigibt."
          className={BUTTON}>
          {progress ?? `Alle Updates vorschlagen (${n})`}
        </button>
      )}
      {(result || error) && (
        <p className="basis-full text-xs" role="status" data-testid={`bulk-result-${hostId}`}>
          {error && <span className="text-red-400">Fehler: {error}</span>}
          {result && (
            result.created > 0 ? (
              <span className="text-emerald-300">
                {result.created === 1 ? "1 Vorschlag" : `${result.created} Vorschläge`} angelegt – freigeben unter „<a href={ACTIONS_PATH} className="underline">Aktionen</a>“
              </span>
            ) : (
              <span className="text-amber-300">Keine Vorschläge angelegt.</span>
            )
          )}
          {result && result.skipped.length > 0 && (
            <span className="block break-words opacity-80">
              Übersprungen: {result.skipped.map((s) => `${s.name} (${s.reason})`).join("; ")}
            </span>
          )}
        </p>
      )}
    </>
  );
}
