import { describe, expect, it } from "vitest";

const nodeFs = "node:" + "fs";
const { readFileSync } = (await import(/* @vite-ignore */ nodeFs)) as { readFileSync: (file: URL, encoding: "utf-8") => string };
const stylePath = "../styles/index.css";
const source = readFileSync(new URL(stylePath, import.meta.url), "utf-8");

const css = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/@tailwind [^;]+;/g, "");

function rule(selector: string): string {
  const match = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].find((m) => m[1].trim() === selector);
  expect(match, selector).toBeDefined();
  return match![2];
}

function property(body: string, name: string): string {
  const value = body.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${name}:`));
  expect(value, name).toBeDefined();
  return value!.slice(name.length + 1).trim();
}

function hex(value: string): number[] {
  expect(value).toMatch(/^#[\da-f]{6}$/i);
  return [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
}

function luminance(rgb: number[]): number {
  const linear = rgb.map((value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

function contrast(first: number[], second: number[]): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe("Darstellung der Anmeldeseite", () => {
  it("zeigt denselben Fokusring an Eingaben, Knöpfen, Links und dem Hilfebereich", () => {
    const focus = rule(".login-page :is(input, button, a, [tabindex]):focus");
    expect(property(focus, "box-shadow")).toBe("0 0 0 2px color-mix(in srgb, var(--color-accent) 35%, transparent)");
    expect(property(focus, "outline")).toBe("none");
    expect(property(rule(".login-page input:focus"), "border-color")).toBe("var(--color-accent)");
  });

  it("zeigt bei ungültigen Eingaben auch mit Fokus einen roten Rand", () => {
    const invalidSelector = '.login-page input[aria-invalid="true"]';
    expect(property(rule(invalidSelector), "border-color")).toBe("rgb(var(--red-400))");
    expect(css.indexOf(invalidSelector)).toBeGreaterThan(css.indexOf(".login-page input:focus"));
  });

  for (const theme of ["simple", "classic", "pro", "original"]) {
    const palette = theme === "simple" ? rule(":root") : rule(`:root[data-theme="${theme}"]`);

    it(`${theme}: weiße Knopfschrift hat auch beim Darüberfahren mindestens 4,5:1 Kontrast`, () => {
      const accent = hex(property(palette, "--color-accent"));
      for (const selector of [".login-submit", ".login-submit:not(:disabled):hover"]) {
        const background = property(rule(selector), "background-color");
        const mix = /^color-mix\(in srgb, var\(--color-accent\) (\d+)%, #000\)$/.exec(background);
        expect(mix).not.toBeNull();
        const rgb = accent.map((channel) => channel * Number(mix![1]) / 100);
        expect(contrast([255, 255, 255], rgb)).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`${theme}: die Copyright-Zeile hat mindestens 4,5:1 Kontrast`, () => {
      const color = property(rule(".login-copyright"), "color");
      const opacity = /^rgb\(var\(--ink\) \/ ([\d.]+)\)$/.exec(color);
      expect(opacity).not.toBeNull();
      const alpha = Number(opacity![1]);
      const ink = property(palette, "--ink").split(" ").map(Number);
      const background = hex(property(palette, "--color-background"));
      const foreground = ink.map((channel, i) => channel * alpha + background[i] * (1 - alpha));
      expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
    });
  }
});
