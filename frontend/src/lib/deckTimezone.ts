/**
 * Zeitzone des Dashboards (`system.timezone`, `GET /me` -> `timezone`), in der Zeitpläne und Wartungsfenster ihre
 * Uhrzeit meinen. Sie liegt auf `window.__nodvardDeck.timezone`, weil Erweiterungsseiten eigene Bundles mit eigenen
 * Modulkopien sind und sich nur `window` teilen.
 */
// Nur React und `window`, keine weiteren Abhängigkeiten: Der Zeitplan-Wähler des UI-Kits holt diese Datei über die
// Kit-Brücke `_shared/frontend/src/timezone.ts`, so landet sie in den Erweiterungs-Bundles.
import { useSyncExternalStore } from "react";

import { deckEventName, dispatchDeckEvent, findDeck } from "./deckGlobal";

/** Zone des Geräts, `"UTC"` wenn der Browser sie nicht nennt. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Zone des Dashboards oder `null`, solange sie nicht geladen ist (älterer Kern, keine Anmeldung). */
export function getDeckTimezone(): string | null {
  const zone = findDeck()?.timezone;
  return typeof zone === "string" && zone ? zone : null;
}

export function setDeckTimezone(zone: string | null): void {
  const shell = findDeck();
  if (!shell) return;
  shell.timezone = zone;
  dispatchDeckEvent("timezone");
}

function subscribe(onChange: () => void): () => void {
  // Genau einen der beiden Namen (der Kern meldet beide): sonst zeichnet sich die Komponente doppelt neu.
  const name = deckEventName("timezone");
  window.addEventListener(name, onChange);
  return () => window.removeEventListener(name, onChange);
}

/** Wie `getDeckTimezone`, aber die Komponente zeichnet sich neu, wenn die Zone wechselt. */
export function useDeckTimezone(): string | null {
  return useSyncExternalStore(subscribe, getDeckTimezone, () => null);
}

/** Die Zone, in der Uhrzeiten gemeint sind, wenn sie von der des Geräts abweicht, sonst `null`. */
export function foreignDeckTimezone(deck: string | null): string | null {
  return deck && deck !== browserTimeZone() ? deck : null;
}
