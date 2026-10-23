import { describe, expect, it } from "vitest";

import { MAX_ROWS, capRows, fitCount, fixedRowsOf, rowsToPx, sizeKeyOf, stageFor } from "./fit";

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

describe("Höhe nach Inhalt: gewählte Größe", () => {
  it("S, M und L sind 2, 3 und 5 Zeilen; alles andere heißt automatisch", () => {
    expect(["s", "m", "l"].map((size) => fixedRowsOf({ size }))).toEqual([2, 3, 5]);
    expect(fixedRowsOf({})).toBeNull();
    expect(fixedRowsOf(undefined)).toBeNull();
    expect(fixedRowsOf({ size: "xl" })).toBeNull();
    expect(fixedRowsOf({ size: "toString" })).toBeNull();
    expect(sizeKeyOf({ size: 3 })).toBeNull();
  });

  it("mit gewählter Größe gibt es nur diese Stufe, passt der Inhalt nicht, null", () => {
    expect(stageFor(60, 1, 3)).toBe(3);
    expect(stageFor(294, 1, 3)).toBe(3);
    expect(stageFor(295, 1, 3)).toBeNull();
    expect(stageFor(400, 1, 5)).toBe(5);
  });

  it("Zahl und Tacho werden nie kleiner als ihre Mindesthöhe", () => {
    expect(capRows(2, 2)).toBe(2);
    expect(capRows(2, 1)).toBe(2);
    expect(capRows(1, null)).toBe(MAX_ROWS);
    expect(stageFor(60, 2, 1)).toBe(2);
  });
});
