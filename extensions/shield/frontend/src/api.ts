/** Gemeinsame Helfer der Nodvard-Shield-Reiter (Aufrufe, Freigabe, Zeitangaben). */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import {
  actionReason, describeActionOutcome, isActionRunning, runAction, RUNNING_IN_BACKGROUND, type ActionState,
} from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { deck } from "../../../_shared/frontend/src/deck";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { EXT_API } from "./ids";

/** Server-Ansicht: Kommt man über die Kachel „Sicherheit“ auf der Server-Seite (`?host=<id>`) her, zeigen alle Reiter
 * nur diesen Server. */
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

/** Ausgang einer Aktion für die Anzeige. `links` führen zur Stelle, an der man weitermacht (Freigabe, Zugang einrichten). */
export interface ActionResult {
  kind: "ok" | "error" | "warn" | "info";
  text: string;
  links?: { href: string; label: string }[];
}

function outcome(kind: ActionResult["kind"], text: string, links: ActionResult["links"] = []): ActionResult {
  return links.length ? { kind, text, links } : { kind, text };
}

/** Holt den Stand der Aktion nach, wenn die erste Antwort den Grund des Scheiterns nicht nennt. */
async function withReason(action: ActionState): Promise<ActionState> {
  const id = action.id ?? action.action_id;
  if (!id || actionReason(action)) return action;
  try {
    const res = await authedFetch(`/actions/${encodeURIComponent(id)}`);
    return res.ok ? { ...action, ...((await res.json()) as ActionState) } : action;
  } catch {
    return action;
  }
}

/** Schlägt die Aktion über das Gate vor und bestätigt sie gleich, wenn der Nutzer freigeben darf. */
export async function proposeAndApprove(path: string, body: unknown, risk: "low" | "medium" | "high", signal?: AbortSignal): Promise<ActionResult> {
  // Die Route nennt keine Risikostufe, für die Freigabe-Prüfung gilt `risk`. Läuft die Aktion länger als die Anfrage
  // wartet, fragt runAction alle 3 s nach, bis `signal` (Seite verlassen) abbricht: dann gilt sie als „läuft im
  // Hintergrund“.
  const { action } = await runAction(path, { method: "POST", body: JSON.stringify(body) }, { risk, signal });
  const id = action.id ?? action.action_id;
  switch (action.status) {
    case "succeeded": {
      // Ein Hinweis der Aktion (etwa „war schon nicht mehr gesperrt“) ersetzt das schlichte „Erledigt.“.
      const note = action.result?.detail?.note;
      return outcome("ok", typeof note === "string" && note ? note : "Erledigt.");
    }
    case "proposed":
      return outcome("warn", "Wartet auf Freigabe unter „Aktionen“.", id ? [{ href: `/actions?action=${encodeURIComponent(id)}`, label: "Zur Freigabe" }] : []);
    case "failed":
    case "denied": {
      const { tone, text } = describeActionOutcome(await withReason(action));
      const needsAccess = text.includes("root-Rechte") && deck().hasPermission("hosts.write");
      // Von einem Menschen abgelehnt ist eine Entscheidung, kein Fehler.
      return outcome(tone === "error" ? "error" : "warn", text, needsAccess ? [{ href: "/settings/hosts", label: "Zugang einrichten" }] : []);
    }
  }
  if (isActionRunning(action.status)) return outcome("info", RUNNING_IN_BACKGROUND);
  return outcome("warn", describeActionOutcome(action).text);
}

/** Schlüssel (z. B. `server:paket`) der Aktionen, die gerade laufen: sperrt deren Knöpfe, ein zweiter Klick startet nichts. */
export interface BusyKeys {
  busy: ReadonlySet<string>;
  begin: (key: string) => boolean;
  end: (key: string) => void;
}

export function useBusyKeys(): BusyKeys {
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const running = useRef(new Set<string>());
  const begin = useCallback((key: string): boolean => {
    if (running.current.has(key)) return false;
    running.current.add(key);
    setBusy(new Set(running.current));
    return true;
  }, []);
  const end = useCallback((key: string) => {
    running.current.delete(key);
    setBusy(new Set(running.current));
  }, []);
  return { busy, begin, end };
}

/** Laufende Aktionen, ihre Hinweise und Abbruch beim Verlassen, gehalten von der Seite: Ein Reiter wird beim Wechsel
 *  abgebaut, die Aktion läuft weiter, bleibt gesperrt und meldet ihr Ergebnis trotzdem. */
export interface PageActions {
  keys: BusyKeys;
  signal: () => AbortSignal;
  /** Setzt den Hinweis der Aktion `key`, `null` nimmt ihn weg. */
  report: (key: string, notice: ActionResult | null) => void;
}

export type ActionNotices = ReadonlyArray<readonly [key: string, notice: ActionResult]>;

/** Ein Hinweis je Aktion: Das Ergebnis ersetzt den Fortschritt an derselben Stelle, andere Aktionen bleiben stehen. */
export function useActionNotices(): { notices: ActionNotices; report: PageActions["report"] } {
  const [notices, setNotices] = useState<ActionNotices>([]);
  const report = useCallback((key: string, notice: ActionResult | null) => {
    setNotices((prev) => {
      const at = prev.findIndex(([k]) => k === key);
      if (!notice) return at < 0 ? prev : prev.filter((_, i) => i !== at);
      return at < 0 ? [...prev, [key, notice]] : prev.map((entry, i) => (i === at ? [key, notice] : entry));
    });
  }, []);
  return { notices, report };
}

export const PageActionsContext = createContext<PageActions | null>(null);

/** Die der Seite; ein Reiter allein hat eigene und zeigt deren Hinweise (`notices`) dann selbst. */
export function usePageActions(): PageActions & { notices: ActionNotices | null } {
  const keys = useBusyKeys();
  const signal = useUnmountSignal();
  const own = useActionNotices();
  const page = useContext(PageActionsContext);
  return page ? { ...page, notices: null } : { keys, signal, report: own.report, notices: own.notices };
}

/** Führt eine Aktion aus, die einen Reiterwechsel überdauert, meldet Fortschritt und Ergebnis unter `key` und gibt die
 * Art des Ergebnisses zurück (`null`, wenn unter `key` schon eine läuft). */
export async function track(
  page: PageActions, key: string, label: string, progress: string, fn: () => Promise<ActionResult>,
): Promise<ActionResult["kind"] | null> {
  if (!page.keys.begin(key)) return null;
  page.report(key, { kind: "info", text: progress });
  try {
    const result = await fn();
    page.report(key, { ...result, text: `${label}: ${result.text}` });
    return result.kind;
  } catch (err) {
    page.report(key, { kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    return "error";
  } finally {
    page.keys.end(key);
  }
}

/** Lädt neu, sobald eine laufende Aktion fertig ist, auch eine aus einem früheren Aufbau des Reiters. */
export function useReloadWhenSettled(busy: ReadonlySet<string>, load: () => void) {
  const before = useRef(busy.size);
  useEffect(() => {
    if (busy.size < before.current) load();
    before.current = busy.size;
  }, [busy, load]);
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

/** "1 Fund", "3 Funde": Zahl mit der passenden Form statt "Fund(e)". */
export function count(n: number, one: string, many: string): string {
  return `${n.toLocaleString("de-DE")} ${n === 1 ? one : many}`;
}
