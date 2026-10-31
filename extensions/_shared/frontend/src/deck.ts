/** Zugriff der Erweiterungsseiten auf die Kern-Shell (docs/02-EXTENSION-API.md). Keine React-Hooks hier (siehe
 * AuthImage.tsx). */
export { deck, deckEventName, findDeck } from "../../../../frontend/src/lib/deckGlobal";

/** CSS-Klasse für das Ziel eines Sprungs von der Server-Seite (`?host=`), Teil des Erweiterungs-Vertrags. */
export const FOCUS_CLASS = "nodvard-deck-focus";
