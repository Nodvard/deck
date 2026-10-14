/**
 * Abbruch beim Verlassen der Seite, damit Nachfragen zu einer Hintergrund-Aktion (`waitForAction`) nach einem
 * Seitenwechsel nicht bis zu einer Stunde weiterlaufen. Der Controller entsteht schon beim Einhängen, nicht erst nach
 * der Freigabe-Antwort (bis zu 20 s), sonst liefe `abort()` beim Abbau ins Leere.
 */
import { useEffect, useRef } from "react";

export function useUnmountSignal(): () => AbortSignal {
  const ref = useRef<AbortController | null>(null);
  if (!ref.current) ref.current = new AbortController();
  useEffect(() => {
    // Im StrictMode läuft der Aufräumschritt einmal vorzeitig, dann frisch anlegen.
    if (ref.current?.signal.aborted) ref.current = new AbortController();
    return () => ref.current?.abort();
  }, []);
  return useRef(() => (ref.current as AbortController).signal).current;
}
