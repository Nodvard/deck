import { describe, expect, it } from "vitest";

import type { DashboardLayoutItem } from "../lib/dashboard";
import { WIDE_COLS, narrowCols, widthChoice, withGroup, withSize, withWidth, withoutWidget } from "./edit";

const item = (over: Partial<DashboardLayoutItem> = {}): DashboardLayoutItem => ({
  widget_id: "w", ext_id: "e", x: 2, y: 1, w: 4, h: 3, config: { group: "apps" }, ...over,
});

describe("Bearbeiten: einzelne Einträge", () => {
  it("schmal ist die Mindestbreite des Typs, mindestens 4; breit sind 8 Spalten", () => {
    expect(narrowCols(2)).toBe(4);
    expect(narrowCols(6)).toBe(6);
    expect(withWidth(item(), true, 6).w).toBe(WIDE_COLS);
    expect(withWidth(item({ w: 8 }), false, 6).w).toBe(6);
    expect(withWidth(item({ w: 8 }), false, 3).w).toBe(4);
  });

  it("breit heißt genau 8 Spalten, schmal höchstens die Mindestbreite, alles dazwischen ist keins von beiden", () => {
    expect(widthChoice(4, 2)).toBe(false);
    expect(widthChoice(2, 2)).toBe(false);
    expect(widthChoice(6, 6)).toBe(false);
    expect(widthChoice(8, 4)).toBe(true);
    expect(widthChoice(7, 6)).toBeNull();
    expect(widthChoice(12, 4)).toBeNull();
  });

  it("eine breite Karte rückt nach links, statt über die zwölfte Spalte zu ragen", () => {
    expect(withWidth(item({ x: 6 }), true, 4)).toMatchObject({ x: 4, w: 8 });
    expect(withWidth(item({ x: 2 }), true, 4)).toMatchObject({ x: 2, w: 8 });
    expect(withWidth(item({ x: 10, w: 2 }), false, 2)).toMatchObject({ x: 8, w: 4 });
  });

  it("die Größe steht in config.size, automatisch nimmt sie weg und lässt den Rest stehen", () => {
    const sized = withSize(item(), "l");
    expect(sized.config).toEqual({ group: "apps", size: "l" });
    expect(withSize(sized, null).config).toEqual({ group: "apps" });
    expect(item().config).toEqual({ group: "apps" });
  });

  it("Gruppe wechseln setzt die Karte ans Ende der neuen Gruppe", () => {
    const moved = withGroup(item(), "security", [item({ y: 0 }), item({ y: 4 })]);
    expect(moved).toMatchObject({ x: 0, y: 5, config: { group: "security" } });
    expect(withGroup(item(), "security", [])).toMatchObject({ x: 0, y: 0 });
  });

  it("Entfernen blendet aus statt zu löschen und behält die übrigen Einstellungen", () => {
    expect(withoutWidget(item()).config).toEqual({ group: "apps", hidden: true });
  });
});
