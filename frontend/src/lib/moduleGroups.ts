/**
 * Gruppen der Modul-Listen: derselbe Aufbau im Einrichtungs-Assistenten (Schritt „Was willst du nutzen?“) und unter
 * Einstellungen → Erweiterungen. Gruppe und Reihenfolge kommen aus dem Manifest der Module (`category`, `sort_order`);
 * der Kern kennt kein Modul beim Namen.
 */

/** Was die Gruppierung von einer Zeile braucht (die API liefert mehr). */
export interface ModuleRow {
  id: string;
  name: string | null;
  category?: string | null;
  sort_order?: number;
}

export interface ModuleGroup<T extends ModuleRow> {
  id: string;
  title: string;
  hint?: string;
  rows: T[];
}

/** Reihenfolge und Überschrift der Gruppen; alles Unbekannte kommt unter „Weitere Module“. */
const GROUPS: { id: string; title: string; hint?: string }[] = [
  { id: "servers", title: "Für deine Server", hint: "Zustand, Zugriff und Verwaltung deiner Rechner und Dienste." },
  { id: "security", title: "Sicherheit" },
  { id: "tools", title: "Werkzeuge" },
  { id: "connections", title: "Verbindungen zu anderen Diensten" },
];
const OTHER_GROUP = { id: "other", title: "Weitere Module" };
/** Beispiel-Module (z. B. „Hello World“): immer die letzte Gruppe. */
const DEVELOPER_GROUP = {
  id: "example",
  title: "Für Entwickler",
  hint: "Beispiele zum Ausprobieren und für die Entwicklung eigener Module. Du brauchst sie nicht.",
};

/** Beispiel-Module für Entwickler (z. B. „Hello World“) gehören nicht in den Assistenten: Wer neu anfängt, würde sich
 * fragen, wozu sie da sind. Unter Einstellungen → Erweiterungen bleiben sie sichtbar (in der Gruppe „Für Entwickler“),
 * damit sich ein eingeschaltetes auch wieder ausschalten lässt. */
export function forEveryone<T extends ModuleRow>(rows: T[]): T[] {
  return rows.filter((r) => r.category !== DEVELOPER_GROUP.id);
}

/** Nach Gruppe, dann `sort_order`, dann Name. Leere Gruppen entfallen. */
export function groupModules<T extends ModuleRow>(rows: T[]): ModuleGroup<T>[] {
  const known = new Set([...GROUPS, DEVELOPER_GROUP].map((g) => g.id));
  const byName = (a: T, b: T) => (a.sort_order ?? 100) - (b.sort_order ?? 100) || (a.name ?? a.id).localeCompare(b.name ?? b.id);
  return [...GROUPS, OTHER_GROUP, DEVELOPER_GROUP]
    .map((g) => ({
      ...g,
      rows: rows.filter((r) => (known.has(r.category ?? "") ? r.category === g.id : g.id === OTHER_GROUP.id)).sort(byName),
    }))
    .filter((g) => g.rows.length > 0);
}
