import { afterEach, describe, expect, it } from "vitest";

import { applyBranding, DEFAULT_ACCENT, type Branding } from "./branding";
import { applyTheme, currentTheme, DEFAULT_THEME, storedTheme, THEME_STORAGE_KEY, THEMES } from "./theme";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() { return data.size; },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => { data.delete(k); },
    setItem: (k, v) => { data.set(k, v); },
  };
}

const BRANDING: Branding = {
  product_name: "Deck", short_name: "Deck", logo_url: null, favicon_url: null, login_subtitle: null, support_url: null,
  colors: { accent: DEFAULT_ACCENT, accent_strong: "#0369a1", background: "#0b1220", surface: "#111a2b", text: "#e6ebf5" },
};

afterEach(() => {
  delete document.documentElement.dataset.theme;
  document.documentElement.removeAttribute("style");
});

describe("Theme", () => {
  it("Standard ist Schlicht dunkel, auch bei unbekanntem oder fehlendem Wert", () => {
    expect(DEFAULT_THEME).toBe("simple");
    expect(storedTheme(memoryStorage())).toBe("simple");
    expect(storedTheme(memoryStorage({ [THEME_STORAGE_KEY]: "neon" }))).toBe("simple");
    expect(currentTheme()).toBe("simple");
  });

  it("setzt data-theme und merkt die Wahl", () => {
    const storage = memoryStorage();
    applyTheme("classic", storage);
    expect(document.documentElement.dataset.theme).toBe("classic");
    expect(currentTheme()).toBe("classic");
    expect(storedTheme(storage)).toBe("classic");
  });

  it("„Bisher“ ist das vierte Theme und wird gemerkt", () => {
    expect(THEMES.map((t) => t.id)).toEqual(["classic", "simple", "pro", "original"]);
    expect(THEMES.find((t) => t.id === "original")).toMatchObject({ label: "Bisher", hint: "Das Aussehen bis 0.8: dunkelblau mit Verlauf." });
    const storage = memoryStorage();
    applyTheme("original", storage);
    expect(currentTheme()).toBe("original");
    expect(storedTheme(storage)).toBe("original");
  });

  it("ein gesperrter Speicher bricht nichts", () => {
    const broken = { getItem: () => { throw new Error("gesperrt"); }, setItem: () => { throw new Error("gesperrt"); } } as unknown as Storage;
    expect(() => applyTheme("pro", broken)).not.toThrow();
    expect(storedTheme(broken)).toBe("simple");
  });
});

describe("Branding und Theme", () => {
  it("die Werkseinstellung der Akzentfarbe überlässt dem Theme die Farben", () => {
    applyBranding(BRANDING);
    const style = document.documentElement.style;
    for (const name of ["--color-accent", "--color-background", "--color-surface", "--color-text"]) {
      expect(style.getPropertyValue(name)).toBe("");
    }
  });

  it("eine eigene Akzentfarbe gilt in jedem Theme, eine Rückkehr zur Werkseinstellung nimmt sie wieder weg", () => {
    applyBranding({ ...BRANDING, colors: { ...BRANDING.colors, accent: "#E11D48" } });
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#e11d48");
    expect(document.documentElement.style.getPropertyValue("--color-background")).toBe("");
    applyBranding(BRANDING);
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("");
  });
});
