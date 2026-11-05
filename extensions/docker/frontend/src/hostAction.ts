import { describeActionOutcome, isActionRunning, runAction } from "../../../_shared/frontend/src/actions";

export interface HostActionResult {
  text: string;
  /** Die Aktion wartet auf Freigabe oder läuft noch. */
  waiting: boolean;
  /** Die Aktion ist gescheitert, abgelehnt oder verfallen; `text` nennt den Grund. */
  failed: boolean;
  /** Kennung der Aktion, solange ihr Ergebnis noch offen ist. */
  actionId?: string;
}

const FAILED = new Set(["failed", "denied", "expired", "dismissed"]);

/** Schlägt eine Aktion für einen Server vor (über das Gate) und sagt in einem Satz, was daraus wurde. */
export async function hostAction(
  hostId: string, actionType: string, reason: string, signal?: AbortSignal, payload: Record<string, string | boolean> = {},
): Promise<HostActionResult> {
  const { action } = await runAction(
    `/hosts/${hostId}/actions/${actionType}`, { method: "POST", body: JSON.stringify({ payload, reason }) }, { signal },
  );
  const status = action.status ?? "?";
  if (status === "proposed") {
    return { text: `vorgeschlagen: Freigabe durch einen Admin nötig, siehe "Aktionen".`, waiting: true, failed: false, actionId: action.id ?? action.action_id };
  }
  if (isActionRunning(status)) {
    return { text: describeActionOutcome(action).text, waiting: true, failed: false, actionId: action.id ?? action.action_id };
  }
  if (status === "succeeded") return { text: action.result?.output ?? "erledigt", waiting: false, failed: false };
  return {
    text: describeActionOutcome(action).text, waiting: false, failed: FAILED.has(status),
  };
}

export async function runHostAction(
  hostId: string, actionType: string, reason: string, signal?: AbortSignal, payload: Record<string, string | boolean> = {},
): Promise<string> {
  return (await hostAction(hostId, actionType, reason, signal, payload)).text;
}
