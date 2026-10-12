/**
 * Zeitzone des Dashboards fuer das Kit (Zeitplan-Waehler): reicht die Funktionen aus
 * `frontend/src/lib/deckTimezone` weiter -- die Logik liegt nur dort, hier wird nichts doppelt.
 * Wie `deck.ts` eine Bruecke zum Kern; der Wert selbst liegt auf `window.__nodvardDeck.timezone`, so
 * sehen Kern und Bundles dieselbe Zone, obwohl jedes Bundle seine eigene Modulkopie hat.
 *
 * Eigene Datei, weil `deck.ts` keine React-Hooks enthalten soll (siehe dort): `useDeckTimezone` ist
 * einer, so aendern sich nur die Bundles, die die Zone wirklich brauchen.
 */
export { browserTimeZone, foreignDeckTimezone, useDeckTimezone } from "../../../../frontend/src/lib/deckTimezone";
