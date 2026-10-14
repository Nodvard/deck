/**
 * Zugriff der Erweiterungsseiten auf die Kern-Shell (docs/02-EXTENSION-API.md): `deck()` liest `__nodvardDeck` oder den
 * alten Namen `__lattice` (dasselbe Objekt), weil ein vor einem Update geöffneter Tab neue Bundles in seinen alten Kern
 * lädt und Fremd-Erweiterungen mit älterem Kit an einem neuen Kern laufen. Keine React-Hooks hier (siehe AuthImage.tsx).
 */
// Ereignisse ebenso: Der Kern feuert beide Namen, das Kit hört auf genau einen (`deckEventName`, siehe location.ts).
export { deck, deckEventName, findDeck } from "../../../../frontend/src/lib/deckGlobal";

/**
 * CSS-Klassen für das Ziel eines Sprungs von der Server-Seite (`?host=`): `nodvard-deck-focus` neu, `lattice-focus` alt
 * (Teil des Erweiterungs-Vertrags). Der Kern kennt beide in einer Regel; den alten setzt das Kit zusätzlich, damit die
 * Markierung auch in einem Tab mit älterem Kern erscheint.
 */
export const FOCUS_CLASS = "nodvard-deck-focus lattice-focus";
