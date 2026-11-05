import { describe, expect, it } from "vitest";

import { naechsteSortierung, sortieren } from "./sortieren";

describe("sortieren", () => {
  it("vergleicht Zahlen numerisch und verändert die Eingabe nicht", () => {
    const zahlen = Object.freeze([10, 2, 0]);
    expect(sortieren(zahlen, (n) => n, "ascending")).toEqual([0, 2, 10]);
    expect(sortieren(zahlen, (n) => n, "descending")).toEqual([10, 2, 0]);
    expect(zahlen).toEqual([10, 2, 0]);
  });

  it("ordnet Umlaute nach deutschen Textregeln ein", () => {
    expect(sortieren(["Zug", "Öl", "Apfel", "Ähre"], (s) => s, "ascending"))
      .toEqual(["Ähre", "Apfel", "Öl", "Zug"]);
  });

  it.each(["ascending", "descending"] as const)("bleibt bei gleichen und fehlenden Werten stabil: %s", (richtung) => {
    const zeilen = [{ id: "a", n: null }, { id: "b", n: 2 }, { id: "c", n: undefined }, { id: "d", n: 2 }, { id: "e", n: NaN }];
    expect(sortieren(zeilen, (s) => s.n, richtung).map((s) => s.id)).toEqual(["b", "d", "a", "c", "e"]);
  });

  it("setzt fehlende Werte auch zwischen unterschiedlichen Zahlen ans Ende", () => {
    expect(sortieren([null, 2, undefined, 10, 0], (n) => n, "descending")).toEqual([10, 2, 0, null, undefined]);
    expect(sortieren([null, 2, undefined, 10, 0], (n) => n, "ascending")).toEqual([0, 2, 10, null, undefined]);
  });
});

it("schaltet aufsteigend, absteigend, Standard und beginnt beim Spaltenwechsel aufsteigend", () => {
  const auf = naechsteSortierung(null, "name");
  expect(auf).toEqual({ spalte: "name", richtung: "ascending" });
  const ab = naechsteSortierung(auf, "name");
  expect(ab).toEqual({ spalte: "name", richtung: "descending" });
  expect(naechsteSortierung(ab, "name")).toBeNull();
  expect(naechsteSortierung(ab, "cpu")).toEqual({ spalte: "cpu", richtung: "ascending" });
});
