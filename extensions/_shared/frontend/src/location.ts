/**
 * Adresszeile (`?tab=`, `?host=`) fuer Erweiterungsseiten: die Seite liest sie nicht nur
 * beim Einhaengen, sondern folgt ihr -- ein Link auf die schon offene Seite (Benachrichtigung,
 * kleine Karte, ntfy-Klick in der App) wechselt so Reiter oder Server-Filter.
 *
 * Die Kern-Shell navigiert per History-API (`pushState`), und das meldet der Browser bei
 * niemandem. Darum feuert `ExtensionPage` (frontend/src/routes/ExtensionPage.tsx) nach jeder
 * Navigation `nodvard-deck:navigate` UND das alte `lattice:navigate` auf `window`, auch nach
 * Zurueck/Vor, und setzt dafuer `window.__nodvardDeck.navigateEvents` (alt: `window.__lattice`,
 * dasselbe Objekt). Ohne diese Marke (Tests, Vorschau, aelterer Kern) folgt der Hook zusaetzlich
 * `popstate`.
 *
 * Der Hook hoert auf GENAU EIN Ereignis: auf `nodvard-deck:navigate`, wenn der Kern
 * `__nodvardDeck` kennt, sonst auf `lattice:navigate` (Tab mit aelterem Kern). Beides zugleich
 * hiesse, jede Navigation doppelt zu zaehlen.
 *
 * Eigene Datei aus demselben Grund wie AuthImage.tsx: nur Bundles, die den Hook wirklich
 * einbinden, aendern sich.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { deckEventName, findDeck } from "./deck";

/** Neuer Name des Ereignisses; der alte (`lattice:navigate`) gilt weiter (Uebergang). Der Kern feuert beide. */
export const NAVIGATE_EVENT = "nodvard-deck:navigate";
/** Alter Name, gilt weiter (Uebergang). */
export const LEGACY_NAVIGATE_EVENT = "lattice:navigate";

/**
 * Aktuelle Query der Adresszeile als `URLSearchParams` (folgt jeder Navigation) und eine
 * Funktion, die einzelne Werte setzt (`null` = entfernen). Geaendert wird per `replaceState`:
 * kein neuer Verlaufseintrag, Neuladen und Lesezeichen behalten den Stand; andere Werte der
 * Query bleiben unberuehrt.
 *
 * Der dritte Wert ist ein Ausloeser: er aendert sich bei jeder Navigation auf die Seite (Link --
 * auch auf genau dieselbe Adresse --, Zurueck/Vor), nicht aber bei Aenderungen ueber den Setter.
 * Als Effekt-Abhaengigkeit fuer das, was ein Link jedes Mal neu ausloesen soll, obwohl es nicht
 * in der Adresse steht: zum Server scrollen, seine Zeile aufklappen. Auf seinen Wert kommt es
 * nicht an (eine neu aufgebaute Seite beginnt wieder bei 0).
 */
export function useUrlParams(): [URLSearchParams, (changes: Record<string, string | null>) => void, number] {
  const [search, setSearch] = useState(() => window.location.search);
  const [visits, setVisits] = useState(0);

  useEffect(() => {
    const sync = () => setSearch(window.location.search);
    const visit = () => {
      sync();
      setVisits((n) => n + 1);
    };
    // In der Kern-Shell kommt Zurueck/Vor als Navigations-Ereignis, und zwar erst, wenn der Router
    // umgeschaltet hat. Selbst schon auf popstate zu reagieren hiesse: die alte Seite zeigt kurz
    // die neue Adresse und fragt den Server ab, bevor der Kern sie neu aufbaut -- doppelt.
    const onPopState = () => {
      if (!findDeck()?.navigateEvents) visit();
    };
    sync(); // zwischen erstem Render und diesem Schritt kann schon navigiert worden sein
    const navigateEvent = deckEventName("navigate"); // genau eines von beiden, siehe oben
    window.addEventListener("popstate", onPopState);
    window.addEventListener(navigateEvent, visit);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener(navigateEvent, visit);
    };
  }, []);

  const params = useMemo(() => new URLSearchParams(search), [search]);

  const update = useCallback((changes: Record<string, string | null>) => {
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    // history.state behalten: darin haengt der Router seinen Verlaufsschluessel.
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    setSearch(window.location.search);
  }, []);

  return [params, update, visits];
}
