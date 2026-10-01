/**
 * Zugriff der Erweiterungsseiten auf die Kern-Shell (docs/02-EXTENSION-API.md §5).
 *
 * Das globale Objekt der Shell heisst `window.__nodvardDeck`, der alte Name `window.__nodvardDeck`
 * gilt weiter (dasselbe Objekt). Seiten und Kit greifen nicht auf einen der Namen zu, sondern
 * ueber `deck()` -- das liest `__nodvardDeck ?? __lattice`. Das ist noetig, weil ein Tab, der vor
 * einem Update geladen wurde, neue Bundles in seinen alten Kern laedt (nur `__lattice`), und
 * weil Fremd-Erweiterungen mit aelterem Kit an einem neuen Kern laufen.
 *
 * Dasselbe gilt fuer die Ereignisse (`nodvard-deck:navigate`, alt `lattice:navigate`): der Kern
 * feuert beide, das Kit hoert auf genau eines (`deckEventName`, siehe location.ts).
 *
 * Keine React-Hooks in dieser Datei (siehe AuthImage.tsx): so aendern sich nur die Bundles, die
 * sie wirklich einbinden.
 */
export { deck, deckEventName, findDeck } from "../../../../frontend/src/lib/deckGlobal";

/**
 * CSS-Klassen fuer das Ziel eines Sprungs von der Server-Seite (`?host=`). `nodvard-deck-focus` ist
 * der neue Name, `lattice-focus` der alte (Teil des Erweiterungs-Vertrags). Der Kern kennt seit
 * diesem Update beide in EINER Regel (frontend/src/styles/index.css); den alten setzen wir
 * zusaetzlich, damit die Markierung auch in einem Tab mit aelterem Kern (alte CSS) erscheint.
 */
export const FOCUS_CLASS = "nodvard-deck-focus lattice-focus";
