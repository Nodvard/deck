/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

/**
 * Feste Farben sehen nur in einem Theme richtig aus: ein dunkles Feld mit `text-white` ist in „Klassisch hell“
 * dunkel auf dunkel, eine Linie in Weiß mit Deckkraft verschwindet auf Weiß. Kern und Module nehmen darum die
 * Variablen des Themes (`var(--color-surface)`, `var(--color-line)`, `rgb(var(--ink) / …)`, `rgb(var(--red-400))`).
 */
// Wie in contract.test.ts: `fs` über einen zusammengesetzten Namen, das Kern-Projekt hat keine Node-Typen.
const nodeFs = "node:" + "fs";
const fs = (await import(/* @vite-ignore */ nodeFs)) as {
  readFileSync: (file: string, encoding: "utf-8") => string;
  readdirSync: (dir: string, options?: { recursive: boolean }) => string[];
  existsSync: (path: string) => boolean;
};
// Über eine Variable: `new URL("…", import.meta.url)` mit festem Text schreibt Vite beim Bauen um.
const testFile = import.meta.url;
const root = decodeURIComponent(new URL("../../../", testFile).pathname);

const FORBIDDEN: { what: string; re: RegExp }[] = [
  { what: "Weiß oder Schwarz mit Deckkraft", re: /rgba?\(\s*(255\s*,\s*255\s*,\s*255|0\s*,\s*0\s*,\s*0)\s*[,)]/ },
  { what: "feste Farbe in einer Klasse", re: /\b[a-z-]+-\[#[0-9a-fA-F]{3,8}\]/ },
  { what: "feste Farbe als Strich oder Füllung", re: /\b(stroke|fill)="#[0-9a-fA-F]{3,8}"/ },
  { what: "Grau aus der festen Tailwind-Palette", re: /\b(bg|text|border|fill|stroke|ring|divide|from|via|to)-(slate|gray|zinc|neutral|stone)-\d{2,3}\b/ },
  // Die hellen Statustöne dunkelt „Klassisch hell“ über die Variablen ab; fest eingetragen wären sie auf Weiß zu blass.
  { what: "helle Statusfarbe fest eingetragen", re: /#(34d399|fbbf24|f87171|6ee7b7|fcd34d|fca5a5)\b/i },
];

/** Bewusst fest, mit Grund. */
const ALLOWED: { file: string; text: string }[] = [
  // Ein QR-Code muss schwarz auf weiß bleiben, sonst liest ihn manche Kamera nicht.
  { file: "frontend/src/components/QrCode.tsx", text: 'fill="#ffffff"' },
  { file: "frontend/src/components/QrCode.tsx", text: 'fill="#000000"' },
  // Terminal und Konsole zeigen den Bildschirm des Servers, der ist dunkel, egal welches Theme gilt.
  { file: "frontend/src/routes/TerminalPage.tsx", text: "bg-[#0b0f14]" },
  { file: "frontend/src/routes/ConsolePage.tsx", text: "bg-[#000]" },
  // Die Vorschau eines Dokuments liegt wie Papier auf Weiß.
  { file: "extensions/documents/frontend/src/DocumentsPage.tsx", text: "bg-[#fff]" },
];

function sourceFiles(): string[] {
  const dirs = ["frontend/src", "extensions/_shared/frontend/src"];
  for (const ext of fs.readdirSync(`${root}extensions`)) {
    if (ext !== "_shared" && fs.existsSync(`${root}extensions/${ext}/frontend/src`)) dirs.push(`extensions/${ext}/frontend/src`);
  }
  return dirs.flatMap((dir) =>
    fs
      .readdirSync(`${root}${dir}`, { recursive: true })
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) && !/(^|\/)(preview|test)\//.test(f))
      .map((f) => `${dir}/${f}`),
  );
}

describe("keine festen Farben in Kern und Modulen", () => {
  it("findet die Quellen (sonst prüfte der Test nichts)", () => {
    const files = sourceFiles();
    expect(files).toContain("frontend/src/components/TimeSeriesChart.tsx");
    expect(files).toContain("extensions/shield/frontend/src/SocPage.tsx");
    expect(files).toContain("extensions/system/frontend/src/LiveView.tsx");
  });

  it("jede Farbe kommt vom Theme, außer den begründeten Ausnahmen", () => {
    const hits: string[] = [];
    for (const file of sourceFiles()) {
      fs.readFileSync(`${root}${file}`, "utf-8").split("\n").forEach((line, i) => {
        for (const { what, re } of FORBIDDEN) {
          const match = re.exec(line);
          if (match && !ALLOWED.some((a) => a.file === file && line.includes(a.text))) hits.push(`${file}:${i + 1} ${what}: ${match[0]}`);
        }
      });
    }
    expect(hits).toEqual([]);
  });

  it("die Ausnahmen gibt es noch (sonst bitte aus der Liste streichen)", () => {
    for (const { file, text } of ALLOWED) expect(fs.readFileSync(`${root}${file}`, "utf-8")).toContain(text);
  });
});
