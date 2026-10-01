import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Die echte Einstiegsdatei (main.tsx): globales Objekt der Kern-Shell unter beiden Namen und
 * Uebernahme der alten Browser-Speicher-Schluessel VOR dem ersten Rendern. Router, Branding und
 * react-dom/client sind ersetzt -- hier laeuft nur die Verdrahtung.
 */
const rendered: Array<{ seenKey: string | null; layoutKey: string | null }> = [];

vi.mock("react-dom/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-dom/client")>();
  return {
    ...actual,
    createRoot: () => ({
      render: () =>
        rendered.push({
          seenKey: window.localStorage.getItem("nodvard-deck.changelogSeen"),
          layoutKey: window.localStorage.getItem("nodvard-deck.console.layout"),
        }),
      unmount: () => {},
    }),
  };
});
vi.mock("./routes/router", () => ({ router: {} }));
vi.mock("./state/branding", () => ({ useBrandingStore: { getState: () => ({ load: async () => {} }) } }));

beforeEach(() => {
  vi.resetModules();
  rendered.length = 0;
  document.body.innerHTML = '<div id="root"></div>';
  window.localStorage.clear();
  delete (window as Partial<Window>).__nodvardDeck;
  delete (window as Partial<Window>).__lattice;
});
afterEach(() => {
  window.localStorage.clear();
  delete (window as Partial<Window>).__nodvardDeck;
  delete (window as Partial<Window>).__lattice;
});

describe("main.tsx: Vertrag mit den Erweiterungs-Bundles", () => {
  it("window.__nodvardDeck und window.__lattice sind DASSELBE Objekt", async () => {
    await import("./main");
    expect(window.__nodvardDeck).toBeDefined();
    expect(window.__nodvardDeck).toBe(window.__lattice);
  });

  it("das Objekt hat die Schluessel, auf die Bundles bauen", async () => {
    await import("./main");
    expect(Object.keys(window.__nodvardDeck).sort()).toEqual(
      [
        "React", "ReactDOM", "ReactJsxRuntime", "confirmDialog", "getAccessToken", "hasPermission",
        "promptDialog", "refreshAccessToken", "refreshAccessTokenResult",
      ].sort(),
    );
    expect(typeof window.__nodvardDeck.getAccessToken).toBe("function");
    expect(typeof window.__nodvardDeck.React.createElement).toBe("function");
    // Aenderungen der Shell (z. B. Zeitzone, Navigations-Marke) sind unter beiden Namen sichtbar.
    window.__nodvardDeck.timezone = "Europe/Berlin";
    expect(window.__lattice.timezone).toBe("Europe/Berlin");
  });

  it("uebernimmt alte Speicher-Schluessel, bevor das erste Mal gerendert wird", async () => {
    window.localStorage.setItem("lattice.changelogSeen", "0.5.0|1");
    window.localStorage.setItem("lattice.console.layout", "us");
    await import("./main");
    expect(rendered).toEqual([{ seenKey: "0.5.0|1", layoutKey: "us" }]);
    expect(window.localStorage.getItem("lattice.changelogSeen")).toBe("0.5.0|1"); // alter bleibt (Rollback)
  });
});
