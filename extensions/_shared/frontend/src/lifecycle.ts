/**
 * Abbruch beim Verlassen der Seite, damit Nachfragen von `runAction` & Co. nach einem Seiten- oder Reiterwechsel nicht
 * bis zu einer Stunde weiterlaufen. Der Aufrufer holt das Signal beim Klick (`unmountSignal()`) und reicht es durch.
 * Eigene Datei wie AuthImage.tsx: Nur Bundles, die den Hook einbinden, ändern sich.
 */
import { useEffect, useRef } from "react";

export function useUnmountSignal(): () => AbortSignal {
  const ref = useRef<AbortController | null>(null);
  if (!ref.current) ref.current = new AbortController();
  useEffect(() => {
    // Im StrictMode läuft der Aufräumschritt einmal vorzeitig -- dann frisch anlegen.
    if (ref.current?.signal.aborted) ref.current = new AbortController();
    return () => ref.current?.abort();
  }, []);
  return useRef(() => (ref.current as AbortController).signal).current;
}
