import { afterEach, describe, expect, it, vi } from "vitest";

import { deck, deckEventName, dispatchDeckEvent, findDeck, installDeckGlobal } from "./deckGlobal";

/** Vertrag zwischen Kern-Shell und Erweiterungs-Bundles (docs/02-EXTENSION-API.md §5). */
const shell = (extra: Partial<NodvardDeckShell> = {}): NodvardDeckShell =>
  ({ getAccessToken: () => "tok", hasPermission: () => true, ...extra }) as NodvardDeckShell;

afterEach(() => {
  delete (window as Partial<Window>).__nodvardDeck;
});

describe("installDeckGlobal", () => {
  it("legt das Objekt unter __nodvardDeck ab", () => {
    const s = shell();
    installDeckGlobal(s);
    expect(window.__nodvardDeck).toBe(s);
  });
});

describe("findDeck / deck", () => {
  it("findet das Objekt der Kern-Shell", () => {
    const s = shell();
    installDeckGlobal(s);
    expect(findDeck()).toBe(s);
    expect(deck()).toBe(s);
  });

  it("ohne Kern-Shell: findDeck liefert undefined, deck wirft", () => {
    expect(findDeck()).toBeUndefined();
    expect(() => deck().hasPermission("x")).toThrow(TypeError);
  });
});

describe("Ereignisse", () => {
  it.each(["navigate", "timezone"] as const)("%s: der Kern meldet das Ereignis genau einmal", (event) => {
    const heard = vi.fn();
    window.addEventListener(`nodvard-deck:${event}`, heard);
    dispatchDeckEvent(event);
    window.removeEventListener(`nodvard-deck:${event}`, heard);
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("ein Zuhörer hört immer auf den neuen Namen, auch ohne Kern-Shell", () => {
    expect(deckEventName("navigate")).toBe("nodvard-deck:navigate");
    installDeckGlobal(shell());
    expect(deckEventName("timezone")).toBe("nodvard-deck:timezone");
  });
});
