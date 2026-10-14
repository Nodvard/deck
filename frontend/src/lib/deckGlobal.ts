/**
 * Der Vertrag zwischen Kern-Shell und Erweiterungs-Bundles: das globale Objekt am Fenster und seine Ereignisse
 * (docs/02-EXTENSION-API.md), jeweils unter dem neuen und dem alten Namen. Nur `window`, keine Abhängigkeiten, weil die
 * Datei über die Kit-Brücke `extensions/_shared/frontend/src/deck.ts` in Erweiterungs-Bundles einkompiliert wird.
 */

/** Ereignisse, die die Kern-Shell auf `window` meldet. */
export type DeckEvent = "navigate" | "timezone";

// Beide Namen gelten: Nach einem Deploy lädt ein noch offener Tab neue Bundles in seinen alten Kern (er kennt nur
// `__lattice`), und Fremd-Erweiterungen mit älterem UI-Kit laufen an einem neuen Kern. Die Kern-Shell setzt darum beide
// Namen auf dasselbe Objekt und feuert jedes Ereignis unter beiden; Zuhörer hören auf genau eines (`deckEventName`).
const EVENT_PREFIX = "nodvard-deck:";
/** Alter Präfix, gilt weiter (Übergang). */
const LEGACY_EVENT_PREFIX = "lattice:";

/** Kern-Shell: das globale Objekt unter beiden Namen ablegen (dasselbe Objekt, kein Abbild). */
export function installDeckGlobal(shell: NodvardDeckShell): void {
  window.__nodvardDeck = shell;
  window.__lattice = shell;
}

/** Das globale Objekt der Kern-Shell, `undefined`, wenn keine läuft (Tests, älteres Fenster). */
export function findDeck(): NodvardDeckShell | undefined {
  if (typeof window === "undefined") return undefined;
  return window.__nodvardDeck ?? window.__lattice;
}

/**
 * Wie `findDeck`, für Seiten, die nur in der Kern-Shell laufen: ohne sie gibt es nichts zu retten, und der Zugriff
 * scheitert mit einem TypeError (wie `window.__lattice.x`).
 */
export function deck(): NodvardDeckShell {
  return findDeck() as NodvardDeckShell;
}

/**
 * Name des Ereignisses, auf das ein Zuhörer hört: der neue, wenn der Kern `__nodvardDeck` kennt, sonst der alte.
 * Beim Anmelden festhalten und beim Abmelden denselben Namen verwenden.
 */
export function deckEventName(event: DeckEvent): string {
  const modern = typeof window !== "undefined" && Boolean(window.__nodvardDeck);
  return (modern ? EVENT_PREFIX : LEGACY_EVENT_PREFIX) + event;
}

/** Kern-Shell: ein Ereignis unter beiden Namen melden (Zuhörer hören immer nur auf einen). */
export function dispatchDeckEvent(event: DeckEvent): void {
  window.dispatchEvent(new Event(EVENT_PREFIX + event));
  window.dispatchEvent(new Event(LEGACY_EVENT_PREFIX + event));
}
