/**
 * Erweiterungen mit ihrem Namen statt ihrer Kennung anzeigen: „Nodvard Shield“ statt „shield“.
 * Die Liste kommt aus `GET /extensions` (jede angemeldete Person darf sie lesen) und wird mit allen
 * anderen Stellen geteilt. Fehlt der Name (Erweiterung entfernt, Liste nicht geladen), steht die Kennung da.
 *
 * Gespeicherte Meldungen, Protokolleinträge und Aufträge tragen die Kennung, die zur Zeit ihrer Entstehung
 * galt. Wurde die Erweiterung seither umbenannt, steht diese alte Kennung in `legacy_ids`: sie führt zum selben Namen.
 */
import { useCallback } from "react";

import { useExtensions, type ExtensionInfo } from "./firstSteps";

/** Die Erweiterung zu einer Kennung, der heutigen oder einer früheren (`legacy_ids`). Die heutige Kennung hat
 * Vorrang: eine frühere kann inzwischen von einer anderen Erweiterung belegt sein. */
export function findExtension(list: readonly ExtensionInfo[] | undefined, id: string): ExtensionInfo | undefined {
  return list?.find((e) => e.id === id) ?? list?.find((e) => e.legacy_ids?.includes(id));
}

/** Der Anzeigename zu einer Kennung; ohne Treffer oder ohne Namen die Kennung selbst. */
export function extensionName(list: readonly ExtensionInfo[] | undefined, id: string): string {
  return findExtension(list, id)?.name ?? id;
}

/** `needed`: nur abfragen, wenn überhaupt eine Kennung anzuzeigen ist (spart den Aufruf auf ruhigen Seiten). */
export function useExtensionLabel(needed = true): (id: string) => string {
  const { data } = useExtensions(needed);
  return useCallback((id: string) => extensionName(data, id), [data]);
}
