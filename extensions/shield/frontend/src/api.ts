/** Gemeinsame Helfer der Nodvard-Shield-Reiter (Aufrufe, Freigabe, Zeitangaben). */
import { createContext, useContext } from "react";

import { isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { EXT_API } from "./ids";

/** Server-Ansicht: kommt man ueber die Kachel "Sicherheit" auf der Server-Seite
 * (`?host=<id>`), zeigen alle Reiter nur diesen Server. */
export const HostFilter = createContext<string | null>(null);

export function useForHost<T extends { host_id: string }>(rows: T[]): T[] {
  const host = useContext(HostFilter);
  return host ? rows.filter((r) => r.host_id === host) : rows;
}

export const API = `${EXT_API}/defender`;

export async function call<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await authedFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body as T;
}

/** Aktion ueber das Gate vorschlagen und -- wer freigeben darf -- gleich bestaetigen. */
export async function proposeAndApprove(path: string, body: unknown, risk: "low" | "medium" | "high", signal?: AbortSignal): Promise<string> {
  // Die Route nennt keine Risikostufe -- fuer die Freigabe-Pruefung gilt `risk`. Laeuft die
  // Aktion laenger als die Anfrage wartet, fragt runAction alle 3 s nach (bis `signal`,
  // das Verlassen der Seite, abbricht -- dann gilt sie als "laeuft im Hintergrund").
  const { action, approved } = await runAction(path, { method: "POST", body: JSON.stringify(body) }, { risk, signal });
  const status = action.status ?? "";
  if (approved) {
    if (status === "succeeded") return "Erledigt.";
    if (isActionRunning(status)) return RUNNING_IN_BACKGROUND;
    return `Fehlgeschlagen: ${action.result?.error ?? status}`;
  }
  if (status === "proposed") return "Wartet auf Freigabe unter „Aktionen“.";
  if (status === "succeeded") return "Erledigt.";
  if (isActionRunning(status)) return RUNNING_IN_BACKGROUND;
  return status === "denied" ? "Von der Sperrliste abgelehnt." : `Status: ${status}`;
}

export function when(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Uhrzeit für "läuft seit …": heute nur "14:03", sonst mit Datum. */
export function clock(ts: number | null | undefined): string {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  if (d.toDateString() === new Date().toDateString()) return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  return when(ts);
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return "nie";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400 * 2) return `vor ${Math.round(s / 3600)} Std.`;
  return `vor ${Math.round(s / 86400)} Tagen`;
}
