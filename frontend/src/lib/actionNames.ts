/**
 * Aktionen mit Namen statt mit Kennungen anzeigen: „Nodvard Shield · Updates einspielen“ statt
 * „shield/<Aktionsart>“. Das Backend liefert beides mit (`ActionOut.ext_name`, `ActionOut.action_label`),
 * solange die Erweiterung installiert und die Aktionsart geladen ist. Fehlt ein Teil (Erweiterung entfernt oder
 * ausgeschaltet, älteres Backend), steht an seiner Stelle die rohe Kennung.
 */
export interface ActionNameSource {
  ext_id: string;
  action_type: string;
  /** Lesbarer Name der Aktionsart. */
  action_label?: string | null;
  /** Name der Erweiterung. */
  ext_name?: string | null;
}

/** Wie die Aktion heißt, wenn der Zusammenhang klar ist (Meldungen und Rückfragen zu genau dieser Aktion). */
export function actionLabel(a: ActionNameSource): string {
  return a.action_label?.trim() || a.action_type;
}

/** `ext_id` der Aktionen, die jemand auf der Server-Seite startet (`POST /hosts/{id}/actions/{type}`): Sie kommen
 * von Nodvard Deck selbst, nicht von einer Erweiterung, darum gibt es zu ihnen keinen `ext_name`. */
const CORE_EXT_ID = "core";

/** Erweiterung und Aktion für Listen: „Nodvard Shield · Updates einspielen“. Ist beides unbekannt, bleibt es bei
 * der Schreibweise mit den rohen Kennungen: `shield/<Aktionsart>`. Befehle von der Server-Seite heißen nur
 * nach der Aktion („Shell-Befehl ausführen“), die Kennung `core` ist kein Name. */
export function actionTitle(a: ActionNameSource): string {
  const extension = a.ext_name?.trim();
  const label = a.action_label?.trim();
  if (!extension && !label) return rawActionTitle(a);
  if (!extension && label && a.ext_id === CORE_EXT_ID) return label;
  return `${extension || a.ext_id} · ${label || a.action_type}`;
}

/** Die rohen Kennungen, z. B. als Tooltip zum Nachschlagen im Protokoll. */
export function rawActionTitle(a: Pick<ActionNameSource, "ext_id" | "action_type">): string {
  return `${a.ext_id}/${a.action_type}`;
}
