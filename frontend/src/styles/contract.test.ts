/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

/**
 * Teile des Vertrags mit Erweiterungs-Bundles, die in Dateien ausserhalb von TypeScript stehen:
 * die CSS-Klasse fuer das Ziel eines Server-Sprungs und die Import-Map in index.html.
 */
// Die Dateien werden gelesen, nicht importiert (ein CSS-Import ist im Test leer). Das Kern-Projekt hat
// keine Node-Typen, darum geht `fs` ueber einen Import mit zusammengesetztem Namen (Typ: any).
const nodeFs = "node:" + "fs";
const { readFileSync } = (await import(/* @vite-ignore */ nodeFs)) as { readFileSync: (file: string, encoding: "utf-8") => string };
const readNextToTest = (relative: string) => readFileSync(decodeURIComponent(new URL(relative, import.meta.url).pathname), "utf-8");
const cssSource = readNextToTest("./index.css");
const html = readNextToTest("../../index.html");
const css = cssSource.replace(/\/\*[\s\S]*?\*\//g, ""); // ohne Kommentare
const block = (selector: string) => {
  const start = css.indexOf(`${selector} {`);
  expect(start).toBeGreaterThanOrEqual(0);
  return css.slice(start, css.indexOf("}", start));
};

describe("index.css", () => {
  it("kennt .nodvard-deck-focus und das alte .lattice-focus in EINER Regel", () => {
    const rule = /(^|\n)([^{}]*\.lattice-focus[^{}]*)\{([^}]*)\}/.exec(css);
    expect(rule).not.toBeNull();
    const selectors = rule![2].split(",").map((s) => s.trim());
    expect(selectors).toEqual([".nodvard-deck-focus", ".lattice-focus"]);
    // Dieselbe Regel hat die Markierung (und nicht nur einen leeren Block).
    expect(rule![3]).toContain("outline:");
    expect(rule![3]).toContain("background-color:");
  });

  it("Leuchten, Gitter und Pulsieren gibt es nur noch im Theme „Bisher“, und dort nur über die Theme-Variablen", () => {
    const original = block(':root[data-theme="original"]');
    expect(original).toContain("radial-gradient");
    const rest = css.replace(original, "");
    for (const name of ["glow", "-grid", "pulse", "radial-gradient"]) expect(rest).not.toContain(name);
    expect(css).not.toContain("pulse");
    // Kein zweiter Satz Bauteile: außerhalb der :root-Blöcke wählt keine Regel ein Theme aus.
    expect(css.replace(/:root\[data-theme="\w+"\]\s*\{/g, "")).not.toContain("data-theme");
  });

  it("in „Bisher“ hellen Knöpfe beim Darüberfahren auf und die Status-Punkte haben ihren Ring, in den anderen Themes nicht", () => {
    // Der Verlauf in „Bisher“ liegt über der Knopffarbe, `hover:bg-accent-strong` allein bliebe dort unsichtbar.
    const standard = block(":root");
    const original = block(':root[data-theme="original"]');
    expect(standard).toContain("--accent-hover-filter: none;");
    expect(original).toContain("--accent-hover-filter: brightness(1.1);");
    const hover = block(':is(button, a, [role="button"]).accent-gradient:not(:disabled):hover');
    expect(hover).toContain("filter: var(--accent-hover-filter);");
    // Wie bis 0.8: 3 px Ring um „online“ und „Problem“, aber ohne Pulsieren.
    for (const ring of ["--status-ok-ring", "--status-bad-ring"]) {
      expect(standard).toContain(`${ring}: none;`);
      expect(original).toMatch(new RegExp(`${ring}: 0 0 0 3px `));
    }
    expect(block(".status-dot.online")).toContain("box-shadow: var(--status-ok-ring);");
    expect(block(".status-dot.problem")).toContain("box-shadow: var(--status-bad-ring);");
    for (const theme of ["classic", "pro"]) {
      const own = block(`:root[data-theme="${theme}"]`);
      expect(own).not.toContain("--accent-hover-filter");
      expect(own).not.toContain("-ring");
    }
  });

  it("die Kern-Klassen für Module bleiben", () => {
    for (const name of [".panel", ".panel-hover", ".accent-gradient", ".accent-soft", ".status-dot", ".nav-active"]) {
      expect(css).toContain(`${name} `);
    }
  });

  it("jedes Theme setzt dieselben Variablen wie der Standard", () => {
    const names = (body: string) => [...body.matchAll(/(--[\w-]+):/g)].map((m) => m[1]);
    const standard = names(block(":root"));
    expect(standard).toContain("--color-accent");
    for (const theme of ["classic", "pro", "original"]) {
      const own = names(block(`:root[data-theme="${theme}"]`));
      expect(own.length).toBeGreaterThan(20);
      for (const name of own) expect(standard).toContain(name);
    }
  });
});

describe("index.html", () => {
  it("die Import-Map zeigt unveraendert auf /lattice-shim/ (die Seite eines alten Tabs verweist darauf)", () => {
    const map = /<script type="importmap">([\s\S]*?)<\/script>/.exec(html);
    expect(map).not.toBeNull();
    expect(JSON.parse(map![1]).imports).toEqual({
      react: "/lattice-shim/react.js",
      "react/jsx-runtime": "/lattice-shim/react-jsx-runtime.js",
      "react-dom": "/lattice-shim/react-dom.js",
    });
  });
});
