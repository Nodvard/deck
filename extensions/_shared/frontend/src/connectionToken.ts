/**
 * Gespeicherte Zugangsdaten einer Verbindung (Proxmox VE, Proxmox-Backups) ändert nur der Kern, mit Passwort und
 * gegebenenfalls Code. Die Verbindungs-Routen der Module lehnen das mit 409 ab.
 */
import { authedFetchConfirmed, errorFromBody } from "./api";

/** Code der Verbindungs-Routen, wenn eine Änderung das hinterlegte Token betrifft. */
export const STORED_SECRET_CODE = "stored_secret";

async function failIfNotOk(res: Response): Promise<void> {
  if (res.ok) return;
  const body = await res.json().catch(() => ({}));
  throw new Error(errorFromBody(body, res.status));
}

/** Löscht das Token unter `label` über den Kern; ohne hinterlegtes Token fragt er nichts. `false` heißt abgebrochen. */
export async function dropConnectionToken(extId: string, label: string): Promise<boolean> {
  const res = await authedFetchConfirmed(
    `/extensions/${extId}/secrets?label=${encodeURIComponent(label)}`,
    (confirmation) => ({ method: "DELETE", body: JSON.stringify(confirmation ?? {}) }),
  );
  if (res === null) return false;
  await failIfNotOk(res);
  return true;
}

/**
 * Merkt den Fingerabdruck der einen Verbindung über den Kern, der dafür nach dem Passwort fragt. Der Kern ändert nur
 * diesen Eintrag, andere Verbindungen und Felder bleiben, wie sie gespeichert sind. `false` heißt abgebrochen.
 */
export async function pinInSettings(extId: string, connection: string, sha256: string): Promise<boolean> {
  const res = await authedFetchConfirmed(`/extensions/${extId}/settings/fingerprint`, (confirmation) => ({
    method: "PUT", body: JSON.stringify({ connection, sha256, ...confirmation }),
  }));
  if (res === null) return false;
  await failIfNotOk(res);
  return true;
}
