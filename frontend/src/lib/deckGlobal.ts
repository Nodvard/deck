/**
 * Der Vertrag zwischen Kern-Shell und Erweiterungs-Bundles: das globale Objekt am Fenster und
 * seine Ereignisse (docs/02-EXTENSION-API.md §5), jeweils unter dem neuen und dem alten Namen.
 *
 *   neu: `window.__nodvardDeck`, `nodvard-deck:navigate`, `nodvard-deck:timezone`
 *   alt: `window.__lattice`,     `lattice:navigate`,      `lattice:timezone`   (gilt weiter)
 *
 * Warum beides? Nach einem Deploy laedt ein noch offener Tab NEUE Bundles in seinen ALTEN Kern
 * (er kennt nur `__lattice`), und Fremd-Erweiterungen mit aelterem UI-Kit laufen an einem NEUEN
 * Kern. Darum:
 *
 * - Die Kern-Shell setzt beide Namen auf DASSELBE Objekt (`installDeckGlobal`) und feuert jedes
 *   Ereignis unter beiden Namen (`dispatchDeckEvent`).
 * - Kit und Bundles lesen `window.__nodvardDeck ?? window.__lattice` (`findDeck`/`deck`) und
 *   hoeren auf GENAU EIN Ereignis (`deckEventName`): auf `nodvard-deck:...`, wenn der Kern den
 *   neuen Namen kennt, sonst auf `lattice:...`. So reagiert nichts doppelt.
 *
 * Bewusst nur `window`, keine Abhaengigkeiten: die Datei wird in Erweiterungs-Bundles einkompiliert.
 */

/** Ereignisse, die die Kern-Shell auf `window` meldet. */
export type DeckEvent = "navigate" | "timezone";

const EVENT_PREFIX = "nodvard-deck:";
/** Alter Praefix, gilt weiter (Uebergang). */
const LEGACY_EVENT_PREFIX = "lattice:";

/** Kern-Shell: das globale Objekt unter beiden Namen ablegen (dasselbe Objekt, kein Abbild). */
export function installDeckGlobal(shell: NodvardDeckShell): void {
  window.__nodvardDeck = shell;
  window.__lattice = shell;
}

/** Das globale Objekt der Kern-Shell, `undefined`, wenn keine laeuft (Tests, aelteres Fenster). */
export function findDeck(): NodvardDeckShell | undefined {
  if (typeof window === "undefined") return undefined;
  return window.__nodvardDeck ?? window.__lattice;
}

/**
 * Wie `findDeck`, fuer Seiten, die nur in der Kern-Shell laufen: ohne sie gibt es nichts zu
 * retten, und der Zugriff scheitert wie bisher `window.__lattice.x` (TypeError).
 */
export function deck(): NodvardDeckShell {
  return findDeck() as NodvardDeckShell;
}

/**
 * Name des Ereignisses, auf das ein Zuhoerer hoert: der neue, wenn der Kern `__nodvardDeck`
 * kennt, sonst der alte. Beim Anmelden festhalten und beim Abmelden denselben Namen verwenden.
 */
export function deckEventName(event: DeckEvent): string {
  const modern = typeof window !== "undefined" && Boolean(window.__nodvardDeck);
  return (modern ? EVENT_PREFIX : LEGACY_EVENT_PREFIX) + event;
}

/** Kern-Shell: ein Ereignis unter beiden Namen melden (Zuhoerer hoeren immer nur auf einen). */
export function dispatchDeckEvent(event: DeckEvent): void {
  window.dispatchEvent(new Event(EVENT_PREFIX + event));
  window.dispatchEvent(new Event(LEGACY_EVENT_PREFIX + event));
}
