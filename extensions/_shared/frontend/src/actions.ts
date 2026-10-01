import { authedFetch, errorFromBody, ServerUnavailableError } from "./api";
import { deck } from "./deck";

/**
 * Genehmigte Aktionen laufen im Hintergrund (core/gate.py). Ist eine
 * Aktion nach etwa 20 s nicht fertig (Update, Installation, langes Skript), antwortet
 * `POST /actions/{id}/approve` mit 202 und Status `executing` -- das ist kein Fehler.
 * Bis die Erweiterungsseiten selbst nachfragen, zeigen sie dafuer diesen Hinweis.
 */
export const RUNNING_IN_BACKGROUND = "Läuft im Hintergrund – das Ergebnis steht unter „Aktionen“.";

/** Status, in denen eine Aktion noch nicht fertig ist. */
export function isActionRunning(status: string | null | undefined): boolean {
  return status === "approved" || status === "executing";
}

/*
 * Der Ablauf "Aktion ausloesen -> wer freigeben darf, gibt selbst
 * frei -> bei 'executing' nachfragen -> Ergebnis" stand in jeder Erweiterungsseite
 * einzeln (mit eigenen Statustexten). Er liegt jetzt hier; die Texte spiegeln
 * frontend/src/lib/actionOutcome.ts des Kerns.
 */
/** Abstand zwischen zwei Nachfragen (wie im Kern, lib/actions.ts). */
export const ACTION_POLL_INTERVAL_MS = 3000;
/** So lange wird hoechstens nachgefragt (ein Update darf bis zu 45 min dauern). */
export const ACTION_POLL_MAX_MS = 60 * 60 * 1000;

/** Status einer Aktion (ActionOut.status) auf Deutsch, klein geschrieben fuer Satzmitte. */
export const ACTION_STATUS_LABEL: Record<string, string> = {
  proposed: "wartet auf Freigabe", approved: "genehmigt", executing: "läuft", succeeded: "abgeschlossen",
  failed: "fehlgeschlagen", denied: "abgelehnt", expired: "abgelaufen", dismissed: "verworfen",
};

export function actionStatusLabel(status: string | null | undefined): string {
  return ACTION_STATUS_LABEL[status ?? ""] ?? status ?? "?";
}

/** Das, was Antworten der Aktions-Routen gemeinsam haben: `ActionOut` des Kerns
 * (`id`) bzw. die kurze Antwort der Erweiterungen (`action_id`). */
export interface ActionState {
  id?: string;
  action_id?: string;
  status?: string | null;
  risk?: string | null;
  detail?: string | null;
  result?: { success?: boolean; output?: string | null; error?: string | null; detail?: Record<string, unknown> | null } | null;
  gate_decision?: { rule?: string | null; detail?: string | null; user_reason?: string | null } | null;
}

export type OutcomeTone = "success" | "error" | "pending" | "neutral";

export interface ActionOutcome {
  tone: OutcomeTone;
  text: string;
}

function nonEmpty(value: string | null | undefined): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Warum eine Aktion nicht lief: Fehlermeldung der Ausfuehrung bzw. Begruendung der
 * Sperre/Ablehnung. `null` bei allen anderen Zustaenden oder ohne Angabe. */
export function actionReason(a: ActionState): string | null {
  if (a.status === "failed") return nonEmpty(a.result?.error);
  if (a.status === "denied") return nonEmpty(a.gate_decision?.detail) ?? nonEmpty(a.gate_decision?.user_reason) ?? nonEmpty(a.detail);
  return null;
}

/** Ergebnis fuer die Anzeige (wie `describeActionOutcome` im Kern). */
export function describeActionOutcome(a: ActionState): ActionOutcome {
  const reason = actionReason(a);
  switch (a.status) {
    case "succeeded":
      return { tone: "success", text: "Ausgeführt" };
    case "failed":
      return { tone: "error", text: `Fehlgeschlagen: ${reason ?? "unbekannter Fehler"}` };
    case "denied":
      if (a.gate_decision?.rule === "user:reject") return { tone: "neutral", text: reason ? `Abgelehnt: ${reason}` : "Abgelehnt" };
      return { tone: "error", text: reason ? `Gesperrt: ${reason}` : "Von einer Sicherheitsregel gesperrt" };
    case "approved":
    case "executing":
      return { tone: "pending", text: "Läuft noch …" };
    case "proposed":
      return { tone: "pending", text: "Wartet auf Freigabe" };
    case "expired":
      return { tone: "neutral", text: "Abgelaufen" };
    case "dismissed":
      return { tone: "neutral", text: "Verworfen" };
    default:
      return { tone: "neutral", text: a.status ? `Unbekannter Zustand (${a.status})` : "Unbekannter Zustand" };
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Abgebrochen", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Abgebrochen", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export interface WaitOptions {
  /** Beim Verlassen der Seite abbrechen (wirft dann einen AbortError). */
  signal?: AbortSignal;
  intervalMs?: number;
  maxMs?: number;
}

/**
 * Fragt `GET /actions/{id}` alle paar Sekunden ab, bis die Aktion fertig ist, und
 * liefert ihren Endstand. Kurze Aussetzer (Neustart, WLAN weg, 5xx) zaehlen nicht als
 * Ergebnis. Nach `maxMs` wird der letzte bekannte Stand geliefert (dann noch laufend),
 * ein 4xx (Aktion weg, keine Rechte) wirft.
 */
export async function waitForAction(
  actionId: string,
  last: ActionState = {},
  { signal, intervalMs = ACTION_POLL_INTERVAL_MS, maxMs = ACTION_POLL_MAX_MS }: WaitOptions = {},
): Promise<ActionState> {
  const deadline = Date.now() + maxMs;
  let state = last;
  for (;;) {
    await sleep(intervalMs, signal);
    // Im Hintergrund-Tab pausieren: jede Nachfrage koennte bei 401 das Token erneuern,
    // und zwei Tabs, die gleichzeitig erneuern, melden sich gegenseitig ab
    // (lib/tokenRefresh.ts). Beim Zurueckkehren geht es beim naechsten Takt weiter.
    if (typeof document !== "undefined" && document.hidden) {
      if (Date.now() >= deadline) return state;
      continue;
    }
    try {
      const res = await authedFetch(`/actions/${encodeURIComponent(actionId)}`, { signal });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        state = { ...state, ...(body as ActionState) };
        if (!isActionRunning(state.status)) return state;
      } else if (res.status < 500) {
        throw new Error(errorFromBody(body, res.status));
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw err;
      // Server gerade nicht erreichbar (Neustart, WLAN weg, Token gerade nicht erneuerbar):
      // weiter fragen. Alles andere ist ein echter Fehler.
      if (!(err instanceof ServerUnavailableError)) throw err;
    }
    if (Date.now() >= deadline) return state;
  }
}

export interface RunActionOptions {
  /** Risikostufe, falls die Antwort der Route keine nennt (Pruefung der Freigabe-Berechtigung). */
  risk?: string;
  /** Nur vorschlagen, nie selbst freigeben. */
  approve?: boolean;
  signal?: AbortSignal;
  pollIntervalMs?: number;
  pollMaxMs?: number;
}

export interface ActionRun extends ActionOutcome {
  /** Letzter bekannter Stand der Aktion. */
  action: ActionState;
  /** Wurde hier freigegeben (nicht schon vorher, nicht offen gelassen). */
  approved: boolean;
}

async function readJson(res: Response): Promise<unknown> {
  return res.json().catch(() => ({}));
}

/**
 * Fuehrt einen schon angelegten Vorschlag zu Ende: gibt ihn -- wenn der Nutzer
 * `actions.approve:<risk>` hat -- selbst frei und fragt bei 'executing' alle 3 s nach,
 * bis die Aktion fertig ist. Fuer Seiten, die den ersten Aufruf selbst machen (z. B.
 * mit Rueckfrage bei knappem Speicher); sonst gleich `runAction`.
 */
export async function settleAction(proposal: ActionState, options: RunActionOptions = {}): Promise<ActionRun> {
  let action = proposal;
  const id = action.id ?? action.action_id;
  let approved = false;

  if (action.status === "proposed" && id && options.approve !== false) {
    const risk = action.risk ?? options.risk;
    if (risk && deck().hasPermission(`actions.approve:${risk}`)) {
      const approveRes = await authedFetch(`/actions/${id}/approve`, { method: "POST" });
      const approveBody = await readJson(approveRes);
      if (!approveRes.ok) throw new Error(errorFromBody(approveBody, approveRes.status));
      action = { ...action, ...(approveBody as ActionState) };
      approved = true;
    }
  }

  if (id && isActionRunning(action.status)) {
    try {
      action = await waitForAction(id, action, {
        signal: options.signal, intervalMs: options.pollIntervalMs, maxMs: options.pollMaxMs,
      });
    } catch (err) {
      // Seite verlassen: kein Fehler, die Aktion laeuft auf dem Server weiter.
      if (!(err instanceof DOMException && err.name === "AbortError")) throw err;
      return { tone: "pending", text: RUNNING_IN_BACKGROUND, action, approved };
    }
  }

  return { ...describeActionOutcome(action), action, approved };
}

/**
 * Loest eine Aktion ueber eine Erweiterungs-Route aus (`POST` legt einen Vorschlag an,
 * ggf. schon mit Ergebnis) und fuehrt sie mit `settleAction` zu Ende. Fehler der
 * Anfragen werfen einen Error mit deutscher Meldung; ein Fehlschlag der Aktion selbst
 * ist ein normales Ergebnis (`tone: "error"`).
 */
export async function runAction(path: string, init: RequestInit = {}, options: RunActionOptions = {}): Promise<ActionRun> {
  const res = await authedFetch(path, init);
  const body = await readJson(res);
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return settleAction(body as ActionState, options);
}
