/**
 * Abbruch beim Verlassen der Seite: laufende Nachfragen von `runAction`
 * & Co. sollen nach einem Seiten- oder Reiterwechsel nicht bis zu einer Stunde
 * weiterlaufen. Der Aufrufer holt sich das Signal erst beim Klick (`unmountSignal()`)
 * und reicht es als `signal` durch.
 *
 * Eigene Datei aus demselben Grund wie AuthImage.tsx: nur Bundles, die den Hook
 * wirklich einbinden, aendern sich.
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
