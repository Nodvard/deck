/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

import { EXTENSION_GUIDE, GUIDE_PATH, GUIDE_SECTIONS, REPO_URL, guideUrl, repoFile } from "./docs";

// Das Kern-Projekt hat keine Node-Typen, darum gehen `fs` und `path` über Importe mit zusammengesetztem Namen
// (wie in lib/thirdParty.test.ts).
const nodeFs = "node:" + "fs";
const fs = (await import(/* @vite-ignore */ nodeFs)) as {
  readFileSync: (file: string, encoding: "utf-8") => string;
  readdirSync: (dir: string, options: { recursive: true }) => string[];
};
const here = (relative: string) => decodeURIComponent(new URL(relative, import.meta.url).pathname);
const read = (relative: string) => fs.readFileSync(here(relative), "utf-8");

/** Die Anker, die GitHub zu den Überschriften einer Markdown-Datei vergibt: klein geschrieben, alles außer Buchstaben,
 * Ziffern, Leerzeichen, „-“ und „_“ weg, Leerzeichen zu „-“; Umlaute bleiben. Gleiche Überschriften bekommen „-1“, „-2“ ...
 * Überschriften in Codeblöcken zählen nicht (dort stehen Kommentare wie „# Einrichtungscode: …“). */
export function githubAnchors(markdown: string): string[] {
  const seen = new Map<string, number>();
  const anchors: string[] = [];
  let fenced = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    const heading = !fenced ? line.match(/^#{1,6}\s+(.*?)\s*#*\s*$/) : null;
    if (!heading) continue;
    const text = heading[1].replace(/`/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
    const base = text.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    anchors.push(count === 0 ? base : `${base}-${count}`);
  }
  return anchors;
}

describe("githubAnchors", () => {
  it("bildet die Anker wie GitHub: Satzzeichen weg, Umlaute bleiben, ein Gedankenstrich lässt „--“ übrig", () => {
    const md = [
      "# Erst-Einrichtung – Schritt für Schritt",
      "## 2. Einrichtungsassistent (/setup)",
      "### 10.1 In der Oberfläche (empfohlen)",
      "### 5.2 SSH-Zugang einrichten – drei Wege",
      "```",
      "# kein Abschnitt",
      "```",
      "## Doppelt",
      "## Doppelt",
    ].join("\n");
    expect(githubAnchors(md)).toEqual([
      "erst-einrichtung--schritt-für-schritt", "2-einrichtungsassistent-setup", "101-in-der-oberfläche-empfohlen",
      "52-ssh-zugang-einrichten--drei-wege", "doppelt", "doppelt-1",
    ]);
  });
});

describe("Links in die Anleitung", () => {
  const guide = read(`../../../${GUIDE_PATH}`);
  const anchors = new Set(githubAnchors(guide));

  it("die Anleitung gibt es, und jeder Anker aus GUIDE_SECTIONS ist eine Überschrift darin", () => {
    expect(anchors.size).toBeGreaterThan(20);
    const missing = Object.entries(GUIDE_SECTIONS).filter(([, anchor]) => !anchors.has(anchor)).map(([key, anchor]) => `${key}: #${anchor}`);
    expect(missing, "Überschrift in der Anleitung umbenannt? Dann den Anker in lib/docs.ts nachziehen").toEqual([]);
  });

  it("jeder Abschnitt, den eine Erweiterung nennt, gibt es", () => {
    for (const [ext, section] of Object.entries(EXTENSION_GUIDE)) expect(GUIDE_SECTIONS[section], ext).toBeTruthy();
  });

  it("fest eingetragene Anker auf die Anleitung im Quelltext der Oberfläche gibt es ebenfalls", () => {
    const files = fs.readdirSync(here("../"), { recursive: true }).filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f));
    expect(files.length).toBeGreaterThan(50);
    const wrong: string[] = [];
    for (const file of files) {
      const text = read(`../${file}`);
      for (const match of text.matchAll(/11-ERST-EINRICHTUNG\.md#([^\s"'`)]+)/g)) {
        if (!anchors.has(match[1])) wrong.push(`${file}: #${match[1]}`);
      }
    }
    expect(wrong).toEqual([]);
  });

  it("baut die Adressen auf dem öffentlichen Quellcode auf", () => {
    expect(REPO_URL).toBe("https://github.com/nodvard/deck");
    expect(repoFile("deploy/README.md", "update-helfer")).toBe("https://github.com/nodvard/deck/blob/main/deploy/README.md#update-helfer");
    expect(guideUrl()).toBe("https://github.com/nodvard/deck/blob/main/docs/11-ERST-EINRICHTUNG.md");
    expect(guideUrl("locked")).toBe("https://github.com/nodvard/deck/blob/main/docs/11-ERST-EINRICHTUNG.md#12-ausgesperrt-notfall-befehle");
  });

  it("verlinkt nur die deutsche Anleitung", () => {
    expect(GUIDE_PATH).not.toContain("/en/");
  });
});
