/**
 * Adresszeile (`?tab=`, `?host=`) für Erweiterungsseiten: Die Seite folgt ihr, statt sie nur beim Einhängen zu lesen,
 * damit ein Link auf die schon offene Seite (Meldung, Karte, ntfy-Klick) Reiter oder Server-Filter wechselt. Die
 * Kern-Shell meldet dazu jede Navigation (`ExtensionPage`). Eigene Datei wie AuthImage.tsx (Hooks nicht in ui.tsx).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { deckEventName, findDeck } from "./deck";

/** Neuer Name des Ereignisses; der alte (`lattice:navigate`) gilt weiter (Übergang). Der Kern feuert beide. */
export const NAVIGATE_EVENT = "nodvard-deck:navigate";
/** Alter Name, gilt weiter (Übergang). */
export const LEGACY_NAVIGATE_EVENT = "lattice:navigate";

/**
 * Aktuelle Query als `URLSearchParams` und ein Setter (`null` = entfernen; per `replaceState`, also ohne neuen
 * Verlaufseintrag, andere Werte bleiben). Der dritte Wert ändert sich bei jeder Navigation auf die Seite, nicht beim
 * Setter: als Effekt-Abhängigkeit für das, was ein Link jedes Mal auslösen soll (zum Server scrollen, Zeile aufklappen).
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
    // In der Kern-Shell (Marke `navigateEvents`) kommt Zurück/Vor als Navigations-Ereignis, erst wenn der Router
    // umgeschaltet hat; selbst auf popstate zu reagieren hieße, die alte Seite fragte die neue Adresse ab, bevor der
    // Kern sie neu aufbaut. Ohne die Marke (Tests, Vorschau, älterer Kern) zählt popstate.
    const onPopState = () => {
      if (!findDeck()?.navigateEvents) visit();
    };
    sync(); // zwischen erstem Render und diesem Schritt kann schon navigiert worden sein
    const navigateEvent = deckEventName("navigate"); // genau eines, sonst zählte jede Navigation doppelt
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
    // history.state behalten: darin hängt der Router seinen Verlaufsschlüssel.
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    setSearch(window.location.search);
  }, []);

  return [params, update, visits];
}
