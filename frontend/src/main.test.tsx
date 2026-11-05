import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Die echte Einstiegsdatei (main.tsx): das globale Objekt der Kern-Shell. Router, Branding und react-dom/client sind
 * ersetzt, hier läuft nur die Verdrahtung. */
vi.mock("react-dom/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-dom/client")>();
  return {
    ...actual,
    createRoot: () => ({
      render: () => {},
      unmount: () => {},
    }),
  };
});
vi.mock("./routes/router", () => ({ router: {} }));
vi.mock("./state/branding", () => ({ useBrandingStore: { getState: () => ({ load: async () => {} }) } }));

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = '<div id="root"></div>';
  window.localStorage.clear();
  delete (window as Partial<Window>).__nodvardDeck;
});
afterEach(() => {
  window.localStorage.clear();
  delete (window as Partial<Window>).__nodvardDeck;
});

describe("main.tsx: Vertrag mit den Erweiterungs-Bundles", () => {
  it("legt das Objekt unter window.__nodvardDeck ab", async () => {
    await import("./main");
    expect(window.__nodvardDeck).toBeDefined();
  });

  it("das Objekt hat die Schlüssel, auf die Bundles bauen", async () => {
    await import("./main");
    expect(Object.keys(window.__nodvardDeck).sort()).toEqual(
      [
        "React", "ReactDOM", "ReactJsxRuntime", "confirmDialog", "confirmIdentity", "getAccessToken", "getProductName", "hasPermission",
        "promptDialog", "refreshAccessToken", "refreshAccessTokenResult",
      ].sort(),
    );
    expect(typeof window.__nodvardDeck.getAccessToken).toBe("function");
    expect(typeof window.__nodvardDeck.React.createElement).toBe("function");
  });

  it("liefert den aktuellen Produktnamen an die Erweiterungen", async () => {
    await import("./main");
    const { useEditionStore, DECK_EDITION } = await import("./state/edition");
    expect(window.__nodvardDeck.getProductName?.()).toBe("Nodvard Deck");
    useEditionStore.setState({ edition: { ...DECK_EDITION, id: "dock", product_name: "Nodvard Dock", choose_modules: false } });
    expect(window.__nodvardDeck.getProductName?.()).toBe("Nodvard Dock");
  });

});
