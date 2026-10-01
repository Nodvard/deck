import { describe, expect, it } from "vitest";

import { ALLOWED_FILTERS, renderTemplate, renderTone } from "./template";

describe("renderTemplate", () => {
  it("gibt ein Literal ohne {{ }} unveraendert zurueck", () => {
    expect(renderTemplate("danger", {})).toBe("danger");
  });

  it("loest einen einfachen Feldpfad auf", () => {
    expect(renderTemplate("{{ title }}", { title: "Hallo" })).toBe("Hallo");
  });

  it("loest einen verschachtelten Feldpfad auf", () => {
    expect(renderTemplate("{{ host.name }}", { host: { name: "host-1" } })).toBe("host-1");
  });

  it("mischt Literal-Text und mehrere Ausdruecke", () => {
    expect(renderTemplate("{{ host }} · {{ count }}", { host: "host-1", count: 3 })).toBe("host-1 · 3");
  });

  it("rendert einen fehlenden Feldpfad als leeren String statt zu werfen", () => {
    expect(renderTemplate("{{ missing.field }}", {})).toBe("");
  });

  it("implementiert exakt die Filterliste aus docs/02 §4", () => {
    expect(ALLOWED_FILTERS.sort()).toEqual(
      ["relative", "datetime", "date", "bytes", "percent", "number", "duration", "tone", "truncate", "upper", "lower"].sort(),
    );
  });

  it("upper/lower", () => {
    expect(renderTemplate("{{ v | upper }}", { v: "abc" })).toBe("ABC");
    expect(renderTemplate("{{ v | lower }}", { v: "ABC" })).toBe("abc");
  });

  it("number formatiert mit deutschem Tausendertrennzeichen", () => {
    expect(renderTemplate("{{ v | number }}", { v: 1234567 })).toBe("1.234.567");
  });

  it("percent haengt % an", () => {
    expect(renderTemplate("{{ v | percent }}", { v: 42.5 })).toBe("42,5%");
  });

  it("bytes waehlt die passende Einheit", () => {
    expect(renderTemplate("{{ v | bytes }}", { v: 1536 })).toBe("1,5 KB");
  });

  it("duration formatiert Sekunden als h/m/s", () => {
    expect(renderTemplate("{{ v | duration }}", { v: 3725 })).toBe("1h 2m");
  });

  it("truncate kuerzt lange Strings", () => {
    const long = "x".repeat(100);
    expect(renderTemplate("{{ v | truncate }}", { v: long }).length).toBeLessThan(long.length);
  });

  it("relative erkennt sehr junge Zeitpunkte", () => {
    expect(renderTemplate("{{ v | relative }}", { v: new Date().toISOString() })).toBe("gerade eben");
  });

  it("tone bildet bekannte Schluesselwoerter ab", () => {
    expect(renderTone("{{ v | tone }}", { v: "critical" })).toBe("danger");
    expect(renderTone("{{ v | tone }}", { v: "ok" })).toBe("good");
    expect(renderTone("{{ v | tone }}", { v: "unbekannt-xyz" })).toBe("neutral");
  });
});
