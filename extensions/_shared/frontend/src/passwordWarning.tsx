/**
 * Hinweis für Formulare, in die jemand einmalig ein fremdes Passwort tippt (z. B. „Proxmox mit Passwort verbinden“): Ohne
 * HTTPS und nicht direkt auf diesem Rechner geht es unverschlüsselt bis zu Nodvard Deck. Regel wie im Kern, keine Sperre
 * (im Heimnetz meist in Ordnung). Eigene Datei, damit sich nur die Bundles ändern, die den Hinweis einbinden.
 */
import type { ReactNode } from "react";

/** Geht, was diese Seite sendet, unverschlüsselt durchs Netz? Nein bei HTTPS und bei diesem Rechner selbst. */
export function sendsInClear(location: Pick<Location, "protocol" | "hostname">): boolean {
  if (location.protocol === "https:") return false;
  const host = location.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return !(host === "localhost" || host === "::1" || /^127\.\d+\.\d+\.\d+$/.test(host));
}

export function PasswordInClearWarning({
  alternative,
  location,
}: {
  /** Was sicherer wäre, als ganzer Satz (z. B. „Sicherer ist dann der Weg mit API-Token.“). */
  alternative?: ReactNode;
  /** Adresse dieser Seite; Vorgabe `window.location`. */
  location?: Pick<Location, "protocol" | "hostname">;
}) {
  const page = location ?? (typeof window === "undefined" ? undefined : window.location);
  if (!page || !sendsInClear(page)) return null;
  return (
    <p role="note" data-testid="password-in-clear" className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
      Achtung: Diese Seite läuft ohne HTTPS. Das Passwort geht unverschlüsselt bis zu Nodvard Deck – im eigenen Heimnetz
      meist in Ordnung, in einem fremden Netz nicht.{alternative ? <> {alternative}</> : null}
    </p>
  );
}
