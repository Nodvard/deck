import { describe, expect, it } from "vitest";

import { forEveryone, groupModules } from "./moduleGroups";

const row = (id: string, category: string | null | undefined, sort_order?: number, name: string | null = id) => ({ id, name, category, sort_order });

describe("groupModules", () => {
  it("ordnet nach Gruppe, dann Reihenfolge, dann Name; Unbekanntes unter „Weitere Module“, Beispiele ganz zuletzt", () => {
    const groups = groupModules([
      row("hello", "example", 10), row("c", "tools", 5), row("b", "servers", 20), row("a", "servers", 20, "A zuerst"),
      row("x", "irgendwas"), row("y", undefined), row("d", "servers", 10), row("sec", "security"), row("n", "connections"),
    ]);
    expect(groups.map((g) => [g.id, g.title])).toEqual([
      ["servers", "Für deine Server"], ["security", "Sicherheit"], ["tools", "Werkzeuge"], ["connections", "Verbindungen zu anderen Diensten"],
      ["other", "Weitere Module"], ["example", "Für Entwickler"],
    ]);
    expect(groups[0].rows.map((r) => r.id)).toEqual(["d", "a", "b"]);
    expect(groups[4].rows.map((r) => r.id)).toEqual(["x", "y"]);
    expect(groups[5].rows.map((r) => r.id)).toEqual(["hello"]);
  });

  it("lässt leere Gruppen weg und kommt ohne Module klar", () => {
    expect(groupModules([row("a", "tools")]).map((g) => g.id)).toEqual(["tools"]);
    expect(groupModules([])).toEqual([]);
  });

  it("forEveryone nimmt nur die Beispiel-Module heraus", () => {
    expect(forEveryone([row("hello", "example"), row("a", "tools"), row("b", null)]).map((r) => r.id)).toEqual(["a", "b"]);
  });
});
