import { describe, expect, it } from "vitest";

import {
  applyBranding, type Branding, COPYRIGHT_HOLDER, copyrightLine, DEFAULT_ACCENT, localImageUrl, markFaviconChanged,
} from "./branding";

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

describe("applyBranding: eigene Akzentfarbe", () => {
  const branding = (accent: string): Branding => ({
    product_name: "Deck",
    short_name: "Deck",
    logo_url: null,
    favicon_url: null,
    login_subtitle: null,
    support_url: null,
    colors: { accent, accent_strong: accent, background: "#000000", surface: "#000000", text: "#ffffff" },
  });
  const style = () => document.documentElement.style;

  it("die kräftige Stufe ist dunkler als der Akzent, sonst hätte Hover keine Wirkung", () => {
    applyBranding(branding("#e11d48"));
    expect(style().getPropertyValue("--color-accent")).toBe("#e11d48");
    expect(style().getPropertyValue("--color-accent-strong")).toBe("color-mix(in srgb, #e11d48 85%, black)");
    expect(style().getPropertyValue("--color-accent-strong")).not.toBe(style().getPropertyValue("--color-accent"));
    expect(style().getPropertyValue("--color-accent-text")).toBe("#e11d48");
  });

  it("ohne eigene Farbe gelten wieder die Farben des Themes", () => {
    applyBranding(branding("#e11d48"));
    applyBranding(branding(DEFAULT_ACCENT));
    for (const name of ["--color-accent", "--color-accent-strong", "--color-accent-text"]) expect(style().getPropertyValue(name)).toBe("");
  });
});

describe("localImageUrl: Bilder nur vom eigenen Server (CSP img-src 'self' data: blob:)", () => {
  it("eigene Pfade und eingebettete Bilder bleiben", () => {
    for (const url of ["/api/v1/branding/logo", "/static/symbol.png?v=2", "data:image/png;base64,iVBORw0KGgo=", "DATA:IMAGE/svg+xml;base64,PHN2Zy8+"]) {
      expect(localImageUrl(url)).toBe(url);
    }
  });

  it("fremde Adressen und alles Unklare werden nicht eingesetzt", () => {
    for (const url of [
      "https://cdn.example.org/favicon.png", "http://intern.lan/x.svg", "//example.org/x.png", "/\\example.org/x.png",
      "example.org/x.png", "javascript:alert(1)", "data:text/html,<b>x</b>", "/mit leerzeichen.png", "/tab\t.png", "",
    ]) {
      expect(localImageUrl(url)).toBeNull();
    }
    expect(localImageUrl(null)).toBeNull();
    expect(localImageUrl(undefined)).toBeNull();
  });
});

describe("applyBranding: Favicon", () => {
  const branding = (favicon_url: string | null): Branding => ({
    product_name: "Deck", short_name: "Deck", logo_url: null, favicon_url, login_subtitle: null, support_url: null,
    colors: { accent: DEFAULT_ACCENT, accent_strong: DEFAULT_ACCENT, background: "#000000", surface: "#000000", text: "#ffffff" },
  });
  const href = () => document.getElementById("favicon-link")?.getAttribute("href");

  function withLink(run: () => void) {
    const link = document.createElement("link");
    link.id = "favicon-link";
    link.rel = "icon";
    link.setAttribute("href", "/favicon.ico");
    document.head.appendChild(link);
    try {
      run();
    } finally {
      link.remove();
      markFaviconChanged(0);
    }
  }

  it("setzt ein eigenes Favicon und nach dem Entfernen wieder das Standard-Symbol", () => withLink(() => {
    applyBranding(branding("/api/v1/branding/favicon"));
    expect(href()).toBe("/api/v1/branding/favicon");
    applyBranding(branding(null));
    expect(href()).toBe("/favicon.ico");
  }));

  it("eine alte fremde Adresse wird nicht eingesetzt, es bleibt das Standard-Symbol", () => withLink(() => {
    applyBranding(branding("https://cdn.example.org/favicon.png"));
    expect(href()).toBe("/favicon.ico");
  }));

  it("nach einem Upload hängt eine Version an der Adresse, damit der Browser das neue Bild lädt", () => withLink(() => {
    markFaviconChanged(1234);
    applyBranding(branding("/api/v1/branding/favicon"));
    expect(href()).toBe("/api/v1/branding/favicon?v=1234");
    applyBranding(branding("data:image/png;base64,AA=="));
    expect(href()).toBe("data:image/png;base64,AA==");
  }));
});
