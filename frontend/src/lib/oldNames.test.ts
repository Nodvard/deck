/// <reference types="vite/client" />
import { afterEach, describe, expect, it, vi } from "vitest";

import { installChunkReload } from "./chunkLoad";
import { deckEventName, dispatchDeckEvent, findDeck, installDeckGlobal } from "./deckGlobal";

/** Die Namen aus der Zeit vor Nodvard Deck gelten im Frontend nicht mehr: weder das globale Objekt noch Ereignisse,
 * CSS-Klasse, Import-Map oder Browser-Speicher. */
const OLD = "__lattice";
const win = window as unknown as Record<string, unknown>;

const nodeFs = "node:" + "fs";
const { existsSync, readFileSync } = (await import(/* @vite-ignore */ nodeFs)) as {
  existsSync: (file: string) => boolean;
  readFileSync: (file: string, encoding: "utf-8") => string;
};
const nextToTest = (relative: string) => decodeURIComponent(new URL(relative, import.meta.url).pathname);

afterEach(() => {
  delete (window as Partial<Window>).__nodvardDeck;
  delete win[OLD];
});

describe("alte Namen im Frontend", () => {
  it("die Kern-Shell liegt nur unter __nodvardDeck, der alte Name allein wird nicht gefunden", () => {
    installDeckGlobal({} as NodvardDeckShell);
    expect(win[OLD]).toBeUndefined();
    delete (window as Partial<Window>).__nodvardDeck;
    win[OLD] = {};
    expect(findDeck()).toBeUndefined();
  });

  it.each(["navigate", "timezone"] as const)("%s kommt nur unter dem neuen Namen", (event) => {
    const old = vi.fn();
    window.addEventListener(`lattice:${event}`, old);
    dispatchDeckEvent(event);
    window.removeEventListener(`lattice:${event}`, old);
    expect(old).not.toHaveBeenCalled();
    expect(deckEventName(event)).toBe(`nodvard-deck:${event}`);
  });

  it("die Sperre gegen wiederholtes Neuladen unter dem alten Namen zählt nicht", () => {
    const store = new Map<string, string>([["lattice:chunk-reload-at", "1000000"]]);
    const storage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    } as unknown as Storage;
    const reload = vi.fn();
    const uninstall = installChunkReload(window, { storage: () => storage, reload, now: () => 1_001_000 });
    window.dispatchEvent(new Event("vite:preloadError"));
    uninstall();
    expect(reload).toHaveBeenCalledTimes(1);
    expect([...store.keys()].sort()).toEqual(["lattice:chunk-reload-at", "nodvard-deck:chunk-reload-at"]);
  });

  it("CSS, Import-Map und Shim-Ordner kennen den alten Namen nicht mehr", () => {
    const css = readFileSync(nextToTest("../styles/index.css"), "utf-8");
    const html = readFileSync(nextToTest("../../index.html"), "utf-8");
    expect(css).not.toContain("lattice-focus");
    expect(html).not.toContain("lattice");
    expect(existsSync(nextToTest("../../public/lattice-shim"))).toBe(false);
    expect(existsSync(nextToTest("../../public/nodvard-deck-shim/react.js"))).toBe(true);
  });
});
