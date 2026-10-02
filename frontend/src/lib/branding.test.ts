import { describe, expect, it } from "vitest";

import { COPYRIGHT_HOLDER, copyrightLine } from "./branding";

describe("copyrightLine", () => {
  it("2026: „© 2026 Nico Benks · Nodvard Deck“", () => {
    expect(copyrightLine(new Date("2026-10-01T12:00:00Z"))).toBe("© 2026 Nico Benks · Nodvard Deck");
  });

  it("vor 2026 (falsche Geräte-Uhr) bleibt es bei 2026", () => {
    expect(copyrightLine(new Date("2020-06-01T12:00:00Z"))).toBe("© 2026 Nico Benks · Nodvard Deck");
  });

  it("ab 2027 als Zeitraum", () => {
    expect(copyrightLine(new Date("2027-03-01T12:00:00Z"))).toBe("© 2026–2027 Nico Benks · Nodvard Deck");
    expect(copyrightLine(new Date("2031-03-01T12:00:00Z"))).toBe("© 2026–2031 Nico Benks · Nodvard Deck");
  });

  it("Rechteinhaber und Softwarename sind fest und hängen nicht vom Branding ab", () => {
    expect(COPYRIGHT_HOLDER).toBe("Nico Benks");
    expect(copyrightLine.length).toBeLessThanOrEqual(1); // kein Parameter für einen eigenen Produktnamen
    expect(copyrightLine(new Date("2026-10-01T12:00:00Z"))).toBe("© 2026 Nico Benks · Nodvard Deck");
  });
});
