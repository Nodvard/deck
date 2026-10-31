/**
 * Der Vertrag zwischen Kern-Shell und Erweiterungs-Bundles: das globale Objekt am Fenster und seine Ereignisse
 * (docs/02-EXTENSION-API.md). Nur `window`, keine Abhängigkeiten, weil die Datei über die Kit-Brücke
 * `extensions/_shared/frontend/src/deck.ts` in Erweiterungs-Bundles einkompiliert wird.
 */

/** Ereignisse, die die Kern-Shell auf `window` meldet. */
export type DeckEvent = "navigate" | "timezone";

const EVENT_PREFIX = "nodvard-deck:";

/** Kern-Shell: das globale Objekt am Fenster ablegen. */
export function installDeckGlobal(shell: NodvardDeckShell): void {
  window.__nodvardDeck = shell;
}

/** Das globale Objekt der Kern-Shell, `undefined`, wenn keine läuft (Tests, fremdes Fenster). */
export function findDeck(): NodvardDeckShell | undefined {
  if (typeof window === "undefined") return undefined;
  return window.__nodvardDeck;
}

/**
 * Wie `findDeck`, für Seiten, die nur in der Kern-Shell laufen: ohne sie gibt es nichts zu retten, und der Zugriff
 * scheitert mit einem TypeError (wie `window.__nodvardDeck.x`).
 */
export function deck(): NodvardDeckShell {
  return findDeck() as NodvardDeckShell;
}

/** Name des Ereignisses auf `window`. */
export function deckEventName(event: DeckEvent): string {
  return EVENT_PREFIX + event;
}

/** Kern-Shell: ein Ereignis melden. */
export function dispatchDeckEvent(event: DeckEvent): void {
  window.dispatchEvent(new Event(EVENT_PREFIX + event));
}
