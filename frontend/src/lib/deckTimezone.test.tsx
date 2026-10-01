import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { installDeckGlobal } from "./deckGlobal";
import { getDeckTimezone, setDeckTimezone, useDeckTimezone } from "./deckTimezone";

/**
 * Die Zeitzone des Dashboards liegt auf dem globalen Objekt der Kern-Shell; Erweiterungs-Bundles
 * kompilieren diese Datei ein und laufen auch in Tabs mit aelterem Kern (nur `__lattice`).
 */
let renders = 0;
function Probe() {
  renders += 1;
  return <p data-testid="zone">{useDeckTimezone() ?? "-"}</p>;
}

afterEach(() => {
  cleanup();
  delete (window as Partial<Window>).__nodvardDeck;
  delete (window as Partial<Window>).__lattice;
  renders = 0;
});

describe("Zeitzone des Dashboards", () => {
  it("neuer Kern: Wert und Aenderung unter beiden Namen, die Komponente zeichnet sich je Aenderung einmal neu", () => {
    installDeckGlobal({} as NodvardDeckShell);
    render(<Probe />);
    expect(screen.getByTestId("zone")).toHaveTextContent("-");
    const before = renders;

    act(() => setDeckTimezone("Europe/Berlin"));
    expect(screen.getByTestId("zone")).toHaveTextContent("Europe/Berlin");
    expect(window.__nodvardDeck.timezone).toBe("Europe/Berlin");
    expect(window.__lattice.timezone).toBe("Europe/Berlin");
    // Beide Ereignisse werden gefeuert, die Komponente hoert nur auf eines: ein Neuzeichnen.
    expect(renders).toBe(before + 1);

    act(() => setDeckTimezone("Asia/Tokyo"));
    expect(screen.getByTestId("zone")).toHaveTextContent("Asia/Tokyo");
    expect(renders).toBe(before + 2);
  });

  it("neues Kit mit altem Kern (nur __lattice): findet und aendert die Zeitzone ebenfalls, einmal je Aenderung", () => {
    window.__lattice = { timezone: "Europe/Berlin" } as NodvardDeckShell;
    expect(window.__nodvardDeck).toBeUndefined();
    expect(getDeckTimezone()).toBe("Europe/Berlin");
    render(<Probe />);
    expect(screen.getByTestId("zone")).toHaveTextContent("Europe/Berlin");
    const before = renders;

    act(() => setDeckTimezone("Asia/Tokyo"));
    expect(screen.getByTestId("zone")).toHaveTextContent("Asia/Tokyo");
    expect(window.__lattice.timezone).toBe("Asia/Tokyo");
    expect(renders).toBe(before + 1);
  });

  it("ohne Kern-Shell: keine Zone, setDeckTimezone tut nichts und wirft nicht", () => {
    expect(getDeckTimezone()).toBeNull();
    expect(() => setDeckTimezone("Europe/Berlin")).not.toThrow();
    expect(getDeckTimezone()).toBeNull();
  });

  it("null und leere Werte gelten als nicht gesetzt", () => {
    installDeckGlobal({ timezone: "" } as NodvardDeckShell);
    expect(getDeckTimezone()).toBeNull();
    setDeckTimezone("Europe/Berlin");
    expect(getDeckTimezone()).toBe("Europe/Berlin");
    setDeckTimezone(null);
    expect(getDeckTimezone()).toBeNull();
  });
});
