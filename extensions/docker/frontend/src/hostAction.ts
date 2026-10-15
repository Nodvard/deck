import { ACTION_STATUS_LABEL, runAction } from "../../../_shared/frontend/src/actions";

export interface HostActionResult {
  text: string;
  /** Die Aktion wartet auf die Freigabe durch jemand anderen. */
  waiting: boolean;
}

/** Schlägt eine Aktion für einen Server vor (über das Gate) und sagt in einem Satz, was daraus wurde. */
export async function hostAction(
  hostId: string, actionType: string, reason: string, signal?: AbortSignal, payload: Record<string, string> = {},
): Promise<HostActionResult> {
  const { action, approved } = await runAction(
    `/hosts/${hostId}/actions/${actionType}`, { method: "POST", body: JSON.stringify({ payload, reason }) }, { signal },
  );
  const status = action.status ?? "?";
  if (!approved) {
    if (status === "proposed") return { text: `vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`, waiting: true };
    return { text: action.result?.output ?? ACTION_STATUS_LABEL[status] ?? status, waiting: false };
  }
  if (status === "succeeded") return { text: action.result?.output ?? "erledigt", waiting: false };
  return { text: `${ACTION_STATUS_LABEL[status] ?? status}${action.result?.error ? ` (${action.result.error})` : ""}`, waiting: false };
}

export async function runHostAction(
  hostId: string, actionType: string, reason: string, signal?: AbortSignal, payload: Record<string, string> = {},
): Promise<string> {
  return (await hostAction(hostId, actionType, reason, signal, payload)).text;
}
