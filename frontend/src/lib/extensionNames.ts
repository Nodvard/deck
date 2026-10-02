/**
 * Erweiterungen mit ihrem Namen statt ihrer Kennung anzeigen: „Nodvard Shield“ statt „nexus-soc“.
 * Die Liste kommt aus `GET /extensions` (jede angemeldete Person darf sie lesen) und wird mit allen
 * anderen Stellen geteilt. Fehlt der Name (Erweiterung entfernt, Liste nicht geladen), steht die Kennung da.
 */
import { useCallback } from "react";

import { useExtensions } from "./firstSteps";

/** `needed`: nur abfragen, wenn überhaupt eine Kennung anzuzeigen ist (spart den Aufruf auf ruhigen Seiten). */
export function useExtensionLabel(needed = true): (id: string) => string {
  const { data } = useExtensions(needed);
  return useCallback((id: string) => data?.find((e) => e.id === id)?.name ?? id, [data]);
}
