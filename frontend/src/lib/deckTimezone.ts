/**
 * Zeitzone des Dashboards (Einstellung `system.timezone`, GET /me -> `timezone`).
 *
 * Zeitplaene und Wartungsfenster meinen ihre Uhrzeit in dieser Zone, nicht in der des Geraets.
 * Der Wert liegt auf `window.__nodvardDeck.timezone` (alt: `window.__lattice`, dasselbe Objekt):
 * Erweiterungsseiten sind eigene Bundles mit eigenen Modulkopien, nur `window` teilen sich Kern
 * und Bundles. Aenderungen meldet das Ereignis `nodvard-deck:timezone`, die Kern-Shell feuert
 * zusaetzlich das alte `lattice:timezone`; Zuhoerer nehmen nur eines (lib/deckGlobal.ts).
 *
 * Bewusst nur React und `window`, keine weiteren Abhaengigkeiten: der Zeitplan-Waehler
 * (components/SchedulePicker.tsx) wird in Erweiterungs-Bundles einkompiliert.
 */
import { useSyncExternalStore } from "react";

import { deckEventName, dispatchDeckEvent, findDeck } from "./deckGlobal";

/** Zone des Geraets, `"UTC"` wenn der Browser sie nicht nennt. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Zone des Dashboards oder `null`, solange sie nicht geladen ist (aelterer Kern, keine Anmeldung). */
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

/** Die Zone, in der Uhrzeiten gemeint sind, wenn sie von der des Geraets abweicht, sonst `null`. */
export function foreignDeckTimezone(deck: string | null): string | null {
  return deck && deck !== browserTimeZone() ? deck : null;
}
