import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it, vi } from "vitest";

import { xtermCsp } from "../../xterm.csp";

const filename = resolve(process.cwd(), "node_modules/@xterm/xterm/lib/xterm.mjs");
const source = readFileSync(filename, "utf8");

it("erhält Truecolor, Kontrastfarben und Maße im tatsächlich installierten xterm", () => {
  const transformed = xtermCsp().transform(source, filename);
  const start = transformed.code.indexOf("_addStyle(t,e)");
  const end = transformed.code.indexOf("_isCellInSelection(", start);
  // Nur die wirkliche Bibliotheksmethode ausführen, ohne einen Canvas in jsdom vorzutäuschen.
  const RowFactory = new Function(`return class { ${transformed.code.slice(start, end)} }`)();
  const renderer = new RowFactory();
  const cell = document.createElement("span");
  cell.style.width = "12px";
  cell.style.letterSpacing = "0.5px";
  const setAttribute = vi.spyOn(cell, "setAttribute");
  renderer._addStyle(cell, "color:#123456");
  renderer._addStyle(cell, "background-color:#abcdef");
  expect(cell.style.color).toBe("rgb(18, 52, 86)");
  expect(cell.style.backgroundColor).toBe("rgb(171, 205, 239)");
  renderer._addStyle(cell, "color:#cccccc");
  expect(cell.style.color).toBe("rgb(204, 204, 204)");
  expect(cell.style.backgroundColor).toBe("rgb(171, 205, 239)");
  expect(cell.style.width).toBe("12px");
  expect(cell.style.letterSpacing).toBe("0.5px");
  expect(setAttribute).not.toHaveBeenCalled();
});

it("bricht bei einer unerwarteten oder mehrfach vorhandenen Bibliotheksstelle ab", () => {
  const transform = xtermCsp().transform;
  expect(() => transform("export class Terminal {}", filename)).toThrow("muss geprüft werden");
  expect(() => transform(source + source, filename)).toThrow("muss geprüft werden");
  const transformed = transform(source, filename);
  expect(() => transform(transformed.code, filename)).toThrow("muss geprüft werden");
});

it("verändert fremde Module und andere Dateien des Pakets nicht", () => {
  const transform = xtermCsp().transform;
  expect(transform(source, "/app/src/terminal.mjs")).toBeNull();
  expect(transform(source, filename.replace("@xterm/xterm", "other/xterm"))).toBeNull();
  expect(transform(".xterm {}", filename.replace("lib/xterm.mjs", "css/xterm.css"))).toBeNull();
});
