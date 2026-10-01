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

  it("die internen Klassen heissen nodvard-deck-glow/-grid/-pulse, die alten gibt es nicht mehr", () => {
    for (const name of ["nodvard-deck-glow", "nodvard-deck-grid", "nodvard-deck-pulse"]) expect(css).toContain(name);
    for (const name of ["lattice-glow", "lattice-grid", "lattice-pulse"]) expect(css).not.toContain(name);
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
