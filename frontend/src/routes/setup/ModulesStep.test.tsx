import { describe, expect, it } from "vitest";

import { groupModules, shortDescription } from "./ModulesStep";

describe("shortDescription", () => {
  it("nimmt den ersten Satz", () => {
    expect(shortDescription("Findet Container. Zeigt sie als Kacheln.")).toBe("Findet Container.");
  });

  it("trennt nicht bei „z. B.“", () => {
    expect(shortDescription("Gameserver starten (z. B. Valheim). Server werden über eine Markierung erkannt.")).toBe(
      "Gameserver starten (z. B. Valheim).",
    );
  });

  it("kürzt bei einem Doppelpunkt ab, wenn davor schon ein sinnvoller Satz steht", () => {
    expect(shortDescription("Virenschutz für alle Server: ClamAV-Scans, Quarantäne und mehr.")).toBe("Virenschutz für alle Server.");
    // Zu kurzer Anfang: nicht kürzen.
    expect(shortDescription("Hinweis: wichtig und lang genug für einen Satz.")).toBe("Hinweis: wichtig und lang genug für einen Satz.");
  });

  it("lässt einen Text ohne Satzende ganz stehen und kommt mit leeren Werten klar", () => {
    expect(shortDescription("Web-Terminal per SSH")).toBe("Web-Terminal per SSH");
    expect(shortDescription(null)).toBe("");
    expect(shortDescription("")).toBe("");
  });
});

describe("groupModules", () => {
  const row = (id: string, category: string | null | undefined, sort_order?: number, name = id) => ({
    id, name, state: "disabled", description: null, icon: null, last_error: null, category, sort_order,
  });

  it("ordnet nach Gruppe, dann Reihenfolge, dann Name; Unbekanntes kommt ans Ende", () => {
    const groups = groupModules([
      row("c", "tools", 5), row("b", "servers", 20), row("a", "servers", 20, "A zuerst"), row("x", "irgendwas"), row("y", undefined),
      row("d", "servers", 10),
    ]);
    expect(groups.map((g) => g.id)).toEqual(["servers", "tools", "other"]);
    expect(groups[0].rows.map((r) => r.id)).toEqual(["d", "a", "b"]);
    expect(groups[2].rows.map((r) => r.id)).toEqual(["x", "y"]);
  });
});
