/**
 * Abbruch beim Verlassen der Seite: laufende Nachfragen zu einer
 * Aktion im Hintergrund (`waitForAction`) sollen nach einem Seiten- oder Kachelwechsel
 * nicht bis zu einer Stunde weiterlaufen. Der Abbruch-Controller darf nicht erst nach
 * der Freigabe-Antwort entstehen (die bis zu 20 s dauert) -- sonst laeuft `abort()` beim
 * Abbau ins Leere. Deshalb gibt es ihn schon ab dem Einhaengen; der Aufrufer holt sich
 * das Signal beim Klick (`unmountSignal()`) und reicht es als `signal` durch.
 *
 * Dasselbe Muster wie `extensions/_shared/frontend/src/lifecycle.ts` fuer die
 * Extension-Seiten.
 */
import { useEffect, useRef } from "react";

export function useUnmountSignal(): () => AbortSignal {
  const ref = useRef<AbortController | null>(null);
  if (!ref.current) ref.current = new AbortController();
  useEffect(() => {
    // Im StrictMode laeuft der Aufraeumschritt einmal vorzeitig -- dann frisch anlegen.
    if (ref.current?.signal.aborted) ref.current = new AbortController();
    return () => ref.current?.abort();
  }, []);
  return useRef(() => (ref.current as AbortController).signal).current;
}
