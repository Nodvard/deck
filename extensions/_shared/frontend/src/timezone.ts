/**
 * Zeitzone des Dashboards für das Kit (Zeitplan-Wähler): reicht `frontend/src/lib/deckTimezone` weiter, die Logik liegt
 * nur dort. Der Wert liegt auf `window.__nodvardDeck.timezone`, so sehen Kern und Bundles trotz eigener Modulkopien
 * dieselbe Zone. Eigene Datei, weil `deck.ts` ohne React-Hooks bleiben soll und `useDeckTimezone` einer ist.
 */
export { browserTimeZone, foreignDeckTimezone, useDeckTimezone } from "../../../../frontend/src/lib/deckTimezone";
