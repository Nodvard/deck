/** Laufende Aktionen nach einer 202-Antwort über /actions/{id} abfragen, bis das Ergebnis vorliegt. */
import { ApiError, apiFetch } from "./api";

/** Abstand zwischen zwei Nachfragen. */
export const ACTION_POLL_INTERVAL_MS = 3000;
/** So lange wird höchstens nachgefragt (ein Update darf bis zu 45 min dauern). */
export const ACTION_POLL_MAX_MS = 60 * 60 * 1000;
/** Höchstdauer einer einzelnen Nachfrage samt Antwort; eine hängende zählt wie ein kurzer Aussetzer. */
export const ACTION_REQUEST_TIMEOUT_MS = 30_000;

export const RUNNING_IN_BACKGROUND_TEXT = "Läuft im Hintergrund, das Ergebnis erscheint hier und unter „Aktionen“.";
export const GAVE_UP_WAITING_TEXT = "Läuft seit über einer Stunde, das Ergebnis steht später unter „Aktionen“.";

/** Status, in denen eine Aktion noch nicht fertig ist. */
export function isActionRunning(status: string | null | undefined): boolean {
  return status === "approved" || status === "executing";
}

/** Nach ACTION_POLL_MAX_MS immer noch nicht fertig. */
export class ActionWaitTimeout extends Error {
  constructor() {
    super(GAVE_UP_WAITING_TEXT);
    this.name = "ActionWaitTimeout";
  }
}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

function abortError(): DOMException {
  return new DOMException("Abgebrochen", "AbortError");
}

/** Wirft einen AbortError, wenn `signal` schon abgebrochen ist (Seite verlassen). */
export function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw abortError();
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(abortError());
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
 * Fragt `GET /actions/{id}` alle paar Sekunden ab, bis die Aktion fertig ist, und liefert dann ihren Endstand. Kurze
 * Aussetzer (Neustart des Dashboards, WLAN weg, 5xx) zählen nicht als Ergebnis, es wird einfach weiter gefragt. Wirft
 * `ActionWaitTimeout` nach `maxMs` und einen AbortError bei `signal`.
 */
export async function waitForAction<T extends { status?: string | null }>(
  actionId: string,
  { signal, intervalMs = ACTION_POLL_INTERVAL_MS, maxMs = ACTION_POLL_MAX_MS }: WaitOptions = {},
): Promise<T> {
  const deadline = Date.now() + maxMs;
  for (;;) {
    await sleep(intervalMs, signal);
    // Frist und Abbruch gelten auch, solange die Antwort aussteht: Sonst hinge das Warten an einer stummen Verbindung.
    const request = new AbortController();
    const stop = () => request.abort();
    const timer = setTimeout(stop, Math.max(0, Math.min(ACTION_REQUEST_TIMEOUT_MS, deadline - Date.now())));
    signal?.addEventListener("abort", stop, { once: true });
    const stopped = new Promise<never>((_, reject) => {
      request.signal.addEventListener("abort", () => reject(abortError()), { once: true });
    });
    try {
      const action = await Promise.race([
        apiFetch<T>(`/actions/${encodeURIComponent(actionId)}`, { method: "GET", signal: request.signal }),
        stopped,
      ]);
      if (signal?.aborted) throw abortError();
      if (!isActionRunning(action?.status)) return action;
    } catch (err) {
      if (signal?.aborted) throw abortError();
      // Ein Abbruch hier war die Frist dieser einen Nachfrage: weiter wie bei einem Aussetzer.
      // 4xx (z. B. Aktion gelöscht, keine Rechte mehr) ändert sich durch Warten nicht.
      if (!isAbortError(err) && err instanceof ApiError && err.status < 500) throw err;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", stop);
    }
    if (Date.now() >= deadline) throw new ActionWaitTimeout();
  }
}
