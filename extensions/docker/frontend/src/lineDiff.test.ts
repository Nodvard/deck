import { describe, expect, it } from "vitest";

import { hunks, lineDiff } from "./lineDiff";

describe("lineDiff", () => {
  it("findet geänderte Zeilen", () => {
    const diff = lineDiff("a\nb\nc\nd\n", "a\nB\nc\nd\ne\n");
    expect(diff.added).toBe(2);
    expect(diff.removed).toBe(1);
    expect(diff.lines!.map((l) => `${l.kind}${l.text}`)).toEqual([" a", "-b", "+B", " c", " d", "+e", " "]);
  });

  it("gleich: nichts geändert", () => {
    expect(lineDiff("x\n", "x\n")).toMatchObject({ added: 0, removed: 0 });
  });

  it("sehr große Unterschiede nur als Zahl", () => {
    const before = Array.from({ length: 3000 }, (_, i) => `a${i}`).join("\n");
    const after = Array.from({ length: 3000 }, (_, i) => `b${i}`).join("\n");
    expect(lineDiff(before, after)).toEqual({ added: 3000, removed: 3000, lines: null });
  });
});

describe("hunks", () => {
  it("zeigt Änderungen mit Umgebung und lässt den Rest aus", () => {
    const before = Array.from({ length: 20 }, (_, i) => `z${i}`).join("\n");
    const after = before.replace("z10", "neu");
    const out = hunks(lineDiff(before, after).lines!, 1);
    expect(out.map((l) => (l ? `${l.kind}${l.text}` : "…"))).toEqual(["…", " z9", "-z10", "+neu", " z11", "…"]);
  });
});

describe("sehr große Unterschiede", () => {
  it("eine reine Umstellung gilt nicht als unverändert", () => {
    const before = Array.from({ length: 1600 }, (_, i) => `z${i}`).join("\n");
    const after = before.split("\n").reverse().join("\n");
    const diff = lineDiff(before, after);
    expect(diff.lines).toBeNull();
    expect(diff.added).toBeGreaterThan(0);
    expect(diff.removed).toBe(diff.added);
  });
});
