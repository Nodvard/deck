import { confirmIdentity, type IdentityConfirmation } from "../state/dialogs";
import { ApiError } from "./api";

/** Antwort 403 `confirm_required`: der Aufruf ersetzt oder löscht gespeicherte Zugangsdaten. */
export function confirmationRequest(err: unknown): { message: string; twoFactor: boolean } | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null;
  const body = err.detail as { code?: unknown; detail?: unknown; two_factor?: unknown } | null | undefined;
  if (!body || body.code !== "confirm_required") return null;
  return {
    message: typeof body.detail === "string" ? body.detail : "Bitte zur Bestätigung das aktuelle Passwort eingeben.",
    twoFactor: body.two_factor === true,
  };
}

/** Ergebnis von `withConfirmation`, wenn der Dialog abgebrochen wurde (eine leere Antwort wie 204 ist `undefined`). */
export const CANCELLED: unique symbol = Symbol("abgebrochen");

/**
 * Führt `run` aus. Verlangt der Server eine Bestätigung, fragt der Dialog Passwort und gegebenenfalls Code ab, und `run`
 * läuft damit noch einmal. `CANCELLED` heißt abgebrochen; Fehler (etwa ein falsches Passwort) gehen an den Aufrufer.
 */
export async function withConfirmation<T>(run: (confirmation?: IdentityConfirmation) => Promise<T>): Promise<T | typeof CANCELLED> {
  try {
    return await run();
  } catch (err) {
    const asked = confirmationRequest(err);
    if (!asked) throw err;
    const confirmation = await confirmIdentity(asked.message, asked.twoFactor);
    if (!confirmation) return CANCELLED;
    return await run(confirmation);
  }
}
