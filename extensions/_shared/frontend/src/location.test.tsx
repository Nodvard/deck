import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { dispatchDeckEvent, installDeckGlobal } from "../../../../frontend/src/lib/deckGlobal";
import { LEGACY_NAVIGATE_EVENT, NAVIGATE_EVENT, useUrlParams } from "./location";

function Probe() {
  const [params, update, visits] = useUrlParams();
  return (
    <>
      <p data-testid="tab">{params.get("tab") ?? "-"}</p>
      <p data-testid="host">{params.get("host") ?? "-"}</p>
      <p data-testid="visits">{visits}</p>
      <button onClick={() => update({ tab: "guard" })}>Reiter</button>
      <button onClick={() => update({ host: null })}>Ohne Server</button>
    </>
  );
}

const clearGlobals = () => {
  delete (window as Partial<Window>).__nodvardDeck;
  delete (window as Partial<Window>).__lattice;
};
beforeEach(() => window.history.replaceState({ usr: null, key: "abc", idx: 3 }, "", "/ext/x/page?host=h1"));
afterEach(() => {
  cleanup();
  clearGlobals();
});

/** Eine Navigation wie in der Kern-Shell: neue Adresse (pushState meldet der Browser nicht) und das Ereignis. */
function shellNavigates(url: string, events: string[]) {
  act(() => {
    window.history.pushState(null, "", url);
    for (const name of events) window.dispatchEvent(new Event(name));
  });
}

describe("useUrlParams", () => {
  it("liest die Adresszeile beim Einhängen", () => {
    render(<Probe />);
    expect(screen.getByTestId("host")).toHaveTextContent("h1");
    expect(screen.getByTestId("tab")).toHaveTextContent("-");
  });

  it("holt eine Navigation nach, die zwischen erstem Render und Einhängen des Zuhörers passiert ist", () => {
    // Layout-Effekte laufen vor den normalen Effekten: die Adresse ändert sich, ohne dass der Hook
    // (noch nicht angemeldet) ein Ereignis hört -- ohne den Abgleich beim Einhängen bliebe der alte Stand.
    function Navigator() {
      useLayoutEffect(() => {
        window.history.replaceState(null, "", "/ext/x/page?tab=spaet&host=h9");
      }, []);
      return null;
    }
    render(
      <>
        <Probe />
        <Navigator />
      </>,
    );
    expect(screen.getByTestId("tab")).toHaveTextContent("spaet");
    expect(screen.getByTestId("host")).toHaveTextContent("h9");
  });

  it("folgt dem Navigations-Ereignis (Kern-Shell) und popstate (Zurück/Vor)", () => {
    render(<Probe />);
    act(() => {
      window.history.pushState(null, "", "/ext/x/page?tab=updates");
      dispatchDeckEvent("navigate");
    });
    expect(screen.getByTestId("tab")).toHaveTextContent("updates");
    expect(screen.getByTestId("host")).toHaveTextContent("-");

    act(() => {
      window.history.replaceState(null, "", "/ext/x/page?host=h2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("host")).toHaveTextContent("h2");
    expect(screen.getByTestId("tab")).toHaveTextContent("-");
  });

  it("ändert den Auslöser bei jeder Navigation, auch auf dieselbe Adresse -- bei eigenen Änderungen nicht", () => {
    render(<Probe />);
    expect(screen.getByTestId("visits")).toHaveTextContent("0");
    act(() => {
      window.history.pushState(null, "", "/ext/x/page?host=h1");
      dispatchDeckEvent("navigate");
    });
    expect(screen.getByTestId("visits")).toHaveTextContent("1");
    act(() => window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByTestId("visits")).toHaveTextContent("2");

    fireEvent.click(screen.getByRole("button", { name: "Reiter" }));
    expect(screen.getByTestId("tab")).toHaveTextContent("guard");
    expect(screen.getByTestId("visits")).toHaveTextContent("2");
  });

  it("in der Kern-Shell (navigateEvents) zählt nur das Navigations-Ereignis, popstate nicht zusätzlich", () => {
    installDeckGlobal({ navigateEvents: true } as unknown as NodvardDeckShell);
    render(<Probe />);
    // Zurück: erst popstate, die Shell meldet danach navigate, sobald der Router umgeschaltet hat.
    act(() => {
      window.history.replaceState(null, "", "/ext/x/page?host=h2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("host")).toHaveTextContent("h1");
    expect(screen.getByTestId("visits")).toHaveTextContent("0");
    act(() => dispatchDeckEvent("navigate"));
    expect(screen.getByTestId("host")).toHaveTextContent("h2");
    expect(screen.getByTestId("visits")).toHaveTextContent("1");
  });

  it("die Marke navigateEvents wird auch gefunden, wenn nur der alte Name da ist (aelterer Kern)", () => {
    window.__lattice = { navigateEvents: true } as unknown as NodvardDeckShell;
    render(<Probe />);
    act(() => {
      window.history.replaceState(null, "", "/ext/x/page?host=h2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("visits")).toHaveTextContent("0");
  });

  it("die Ereignisnamen sind die, die die Kern-Shell feuert", () => {
    expect(NAVIGATE_EVENT).toBe("nodvard-deck:navigate");
    expect(LEGACY_NAVIGATE_EVENT).toBe("lattice:navigate");
  });

  describe("hört auf genau ein Navigations-Ereignis", () => {
    it("neuer Kern (__nodvardDeck): nur nodvard-deck:navigate, der Kern-Schlag mit beiden zählt einmal", () => {
      installDeckGlobal({} as NodvardDeckShell);
      render(<Probe />);
      shellNavigates("/ext/x/page?tab=alt", [LEGACY_NAVIGATE_EVENT]);
      expect(screen.getByTestId("visits")).toHaveTextContent("0"); // das alte hört dieses Kit nicht
      shellNavigates("/ext/x/page?tab=neu", [NAVIGATE_EVENT]);
      expect(screen.getByTestId("visits")).toHaveTextContent("1");
      expect(screen.getByTestId("tab")).toHaveTextContent("neu");
      // So feuert ExtensionPage: beide Namen -- das Kit zählt genau einmal.
      shellNavigates("/ext/x/page?tab=beide", [NAVIGATE_EVENT, LEGACY_NAVIGATE_EVENT]);
      expect(screen.getByTestId("visits")).toHaveTextContent("2");
      expect(screen.getByTestId("tab")).toHaveTextContent("beide");
    });

    it("neues Kit mit altem Kern (nur __lattice): hört auf lattice:navigate, der Kern-Schlag zählt einmal", () => {
      window.__lattice = {} as NodvardDeckShell;
      expect(window.__nodvardDeck).toBeUndefined();
      render(<Probe />);
      shellNavigates("/ext/x/page?tab=neu", [NAVIGATE_EVENT]);
      expect(screen.getByTestId("visits")).toHaveTextContent("0"); // einen alten Kern gibt es mit diesem Namen nicht
      shellNavigates("/ext/x/page?tab=alt", [LEGACY_NAVIGATE_EVENT]);
      expect(screen.getByTestId("visits")).toHaveTextContent("1");
      expect(screen.getByTestId("tab")).toHaveTextContent("alt");
      shellNavigates("/ext/x/page?tab=beide", [NAVIGATE_EVENT, LEGACY_NAVIGATE_EVENT]);
      expect(screen.getByTestId("visits")).toHaveTextContent("2");
    });

    it("beim Abmelden wird derselbe Name wieder entfernt, den das Kit angemeldet hat", () => {
      installDeckGlobal({} as NodvardDeckShell);
      const { unmount } = render(<Probe />);
      unmount();
      // Ein ungehörter Schlag danach darf nichts mehr bewirken (kein Fehler wegen eines unmounteten Hooks).
      expect(() => act(() => dispatchDeckEvent("navigate"))).not.toThrow();
    });
  });

  it("update setzt und entfernt einzelne Werte per replaceState und behält Rest, Anker und Verlaufszustand", () => {
    window.history.replaceState({ usr: null, key: "abc", idx: 3 }, "", "/ext/x/page?host=h1&x=a%20b#unten");
    const length = window.history.length;
    render(<Probe />);

    fireEvent.click(screen.getByRole("button", { name: "Reiter" }));
    expect(window.location.pathname + window.location.search + window.location.hash).toBe("/ext/x/page?host=h1&x=a+b&tab=guard#unten");
    expect(screen.getByTestId("tab")).toHaveTextContent("guard");

    fireEvent.click(screen.getByRole("button", { name: "Ohne Server" }));
    expect(window.location.search).toBe("?x=a+b&tab=guard");
    expect(screen.getByTestId("host")).toHaveTextContent("-");

    expect(window.history.length).toBe(length);
    expect(window.history.state).toEqual({ usr: null, key: "abc", idx: 3 });
  });
});
