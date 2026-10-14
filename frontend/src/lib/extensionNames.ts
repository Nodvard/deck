/**
 * Erweiterungen mit ihrem Namen statt ihrer Kennung anzeigen („Nodvard Shield“ statt „shield“); fehlt der Name, steht
 * die Kennung da. Die Liste kommt aus `GET /extensions`. Gespeicherte Meldungen, Protokolleinträge und Aufträge
 * tragen die Kennung von damals; nach einer Umbenennung führt sie über `legacy_ids` zum selben Namen.
 */
import { useCallback } from "react";

import { useExtensions, type ExtensionInfo } from "./firstSteps";

/**
 * Die Erweiterung zu einer Kennung, der heutigen oder einer alten (`legacy_ids`). Die heutige Kennung hat Vorrang:
 * eine alte kann inzwischen von einer anderen Erweiterung belegt sein.
 */
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
