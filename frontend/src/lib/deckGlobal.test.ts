import { afterEach, describe, expect, it, vi } from "vitest";

import { deck, deckEventName, dispatchDeckEvent, findDeck, installDeckGlobal } from "./deckGlobal";

/**
 * Vertrag zwischen Kern-Shell und Erweiterungs-Bundles (docs/02-EXTENSION-API.md §5): das globale
 * Objekt unter zwei Namen, Ereignisse unter zwei Namen -- und Zuhoerer, die auf genau eines hoeren.
 */
const shell = (extra: Partial<NodvardDeckShell> = {}): NodvardDeckShell =>
  ({ getAccessToken: () => "tok", hasPermission: () => true, ...extra }) as NodvardDeckShell;

function clearGlobals() {
  delete (window as Partial<Window>).__nodvardDeck;
  delete (window as Partial<Window>).__lattice;
}
afterEach(clearGlobals);

describe("installDeckGlobal", () => {
  it("legt DASSELBE Objekt unter beiden Namen ab", () => {
    const s = shell();
    installDeckGlobal(s);
    expect(window.__nodvardDeck).toBe(s);
    expect(window.__lattice).toBe(s);
    expect(window.__nodvardDeck === window.__lattice).toBe(true);
  });

  it("was an einem Namen gesetzt wird, sieht man am anderen", () => {
    installDeckGlobal(shell());
    window.__lattice.timezone = "Europe/Berlin";
    window.__nodvardDeck.navigateEvents = true;
    expect(window.__nodvardDeck.timezone).toBe("Europe/Berlin");
    expect(window.__lattice.navigateEvents).toBe(true);
  });
});

describe("findDeck / deck", () => {
  it("neuer Kern (beide Namen): das Objekt", () => {
    const s = shell();
    installDeckGlobal(s);
    expect(findDeck()).toBe(s);
    expect(deck()).toBe(s);
  });

  it("aelterer Kern (nur __lattice): neues Kit findet es trotzdem", () => {
    const old = shell();
    window.__lattice = old;
    expect(window.__nodvardDeck).toBeUndefined();
    expect(findDeck()).toBe(old);
    expect(deck().getAccessToken()).toBe("tok");
  });

  it("nur der neue Name genuegt ebenfalls", () => {
    const s = shell();
    window.__nodvardDeck = s;
    expect(findDeck()).toBe(s);
  });

  it("ohne Kern-Shell: findDeck liefert undefined, deck wirft wie bisher", () => {
    expect(findDeck()).toBeUndefined();
    expect(() => deck().hasPermission("x")).toThrow(TypeError);
  });
});

describe("Ereignisse", () => {
  it("der Kern feuert beide Namen, je einmal", () => {
    const neu = vi.fn();
    const alt = vi.fn();
    window.addEventListener("nodvard-deck:navigate", neu);
    window.addEventListener("lattice:navigate", alt);
    dispatchDeckEvent("navigate");
    window.removeEventListener("nodvard-deck:navigate", neu);
    window.removeEventListener("lattice:navigate", alt);
    expect(neu).toHaveBeenCalledTimes(1);
    expect(alt).toHaveBeenCalledTimes(1);
  });

  it("auch die Zeitzone meldet der Kern unter beiden Namen", () => {
    const neu = vi.fn();
    const alt = vi.fn();
    window.addEventListener("nodvard-deck:timezone", neu);
    window.addEventListener("lattice:timezone", alt);
    dispatchDeckEvent("timezone");
    window.removeEventListener("nodvard-deck:timezone", neu);
    window.removeEventListener("lattice:timezone", alt);
    expect(neu).toHaveBeenCalledTimes(1);
    expect(alt).toHaveBeenCalledTimes(1);
  });

  it("ein Zuhoerer nimmt den neuen Namen, wenn der Kern __nodvardDeck kennt, sonst den alten", () => {
    expect(deckEventName("navigate")).toBe("lattice:navigate");
    expect(deckEventName("timezone")).toBe("lattice:timezone");
    window.__lattice = shell();
    expect(deckEventName("navigate")).toBe("lattice:navigate");
    installDeckGlobal(shell());
    expect(deckEventName("navigate")).toBe("nodvard-deck:navigate");
    expect(deckEventName("timezone")).toBe("nodvard-deck:timezone");
  });

  it.each([
    ["neuer Kern", true],
    ["aelterer Kern (nur __lattice)", false],
  ])("%s: ein Zuhoerer reagiert auf einen Kern-Schlag genau einmal", (_name, modern) => {
    if (modern) installDeckGlobal(shell());
    else window.__lattice = shell();
    const heard = vi.fn();
    const name = deckEventName("navigate");
    window.addEventListener(name, heard);
    dispatchDeckEvent("navigate");
    window.removeEventListener(name, heard);
    expect(heard).toHaveBeenCalledTimes(1);
  });
});

describe("Typen", () => {
  it("LatticeTokenRefreshResult ist ein Alias von NodvardDeckTokenRefreshResult", () => {
    const alt: LatticeTokenRefreshResult = { status: "rejected" };
    const neu: NodvardDeckTokenRefreshResult = alt;
    const zurueck: LatticeTokenRefreshResult = { status: "ok", token: "t" } satisfies NodvardDeckTokenRefreshResult;
    expect([neu.status, zurueck.status]).toEqual(["rejected", "ok"]);
  });
});
