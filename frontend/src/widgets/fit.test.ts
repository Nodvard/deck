import { describe, expect, it } from "vitest";

import { MAX_ROWS, fitCount, rowsToPx, stageFor } from "./fit";

describe("Höhe nach Inhalt: Stufen", () => {
  it("rechnet Rasterzeilen mit Abstand in Pixel um (XS 90, S 192, M 294, L 498)", () => {
    expect([1, 2, 3, 5].map(rowsToPx)).toEqual([90, 192, 294, 498]);
  });

  it("nimmt die kleinste Stufe, die passt, und überspringt die 4", () => {
    expect(stageFor(60)).toBe(1);
    expect(stageFor(90)).toBe(1);
    expect(stageFor(91)).toBe(2);
    expect(stageFor(294)).toBe(3);
    expect(stageFor(295)).toBe(5);
    expect(stageFor(498)).toBe(MAX_ROWS);
  });

  it("passt nicht einmal L: null (dann kürzt die Karte die Liste)", () => {
    expect(stageFor(499)).toBeNull();
  });

  it("Zahl und Tacho: mindestens S, auch wenn der Inhalt in XS passte", () => {
    expect(stageFor(60, 2)).toBe(2);
    expect(stageFor(250, 2)).toBe(3);
  });

  it("zählt die ganzen Zeilen, die passen, mindestens eine", () => {
    expect(fitCount([40, 80, 120, 160], 125)).toBe(3);
    expect(fitCount([40, 80], 80)).toBe(2);
    expect(fitCount([200, 400], 100)).toBe(1);
    expect(fitCount([], 100)).toBe(1);
  });
});
