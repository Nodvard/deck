import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { Link, RouterProvider, createBrowserRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deckEventName, installDeckGlobal } from "../lib/deckGlobal";
import { ExtensionPage } from "./ExtensionPage";

// Die Seite der Erweiterung: liest die Adresszeile wie extensions/_shared/frontend/src/location.ts (beim Einhängen und
// bei jedem Navigations-Ereignis) und zählt Einhängungen und Aufrufe.
let mounts = 0;
let syncs = 0;
function DemoPage() {
  const [search, setSearch] = useState(() => window.location.search);
  useEffect(() => {
    mounts += 1;
    const sync = () => {
      syncs += 1;
      setSearch(window.location.search);
    };
    const name = deckEventName("navigate");
    window.addEventListener(name, sync);
    return () => window.removeEventListener(name, sync);
  }, []);
  // Wie ein Klick im Reiterstreifen: Adresse per replaceState ändern -- der Router erfährt davon nichts.
  function switchTab() {
    window.history.replaceState(window.history.state, "", "/ext/demo/page?tab=updates");
    setSearch(window.location.search);
  }
  return (
    <>
      <p data-testid="query">{search}</p>
      <button onClick={switchTab}>Reiter Updates</button>
    </>
  );
}

vi.mock("../lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "demo", path: "/page", component: "DemoPage" }], isLoading: false }),
}));
// Das Bundle laedt ExtensionPage per import(url) -- hier durch die Demo-Seite ersetzt.
vi.mock("/api/v1/extensions/demo/frontend/index.js?v=dev", () => ({ DemoPage }));

function Menu() {
  const { key } = useLocation();
  return (
    <nav data-key={key}>
      <Link to="/ext/demo/page?tab=guard">Link Guard</Link>
      <Link to="/ext/demo/page">Link ohne Query</Link>
    </nav>
  );
}

function renderApp() {
  const router = createBrowserRouter([
    { path: "/ext/:extId/*", element: <><Menu /><ExtensionPage /></> },
  ]);
  render(<RouterProvider router={router} />);
  return router;
}

beforeEach(() => {
  mounts = 0;
  syncs = 0;
  window.history.replaceState(null, "", "/ext/demo/page");
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Partial<Window>).__nodvardDeck;
});

describe("ExtensionPage meldet Navigationen", () => {
  it("feuert nodvard-deck:navigate nach jedem Link innerhalb der Seite, einmal", async () => {
    renderApp();
    expect(await screen.findByTestId("query")).toHaveTextContent("");
    const neu = vi.fn();
    window.addEventListener("nodvard-deck:navigate", neu);

    fireEvent.click(screen.getByRole("link", { name: "Link Guard" }));
    await vi.waitFor(() => expect(neu).toHaveBeenCalled());
    expect(screen.getByTestId("query")).toHaveTextContent("?tab=guard");
    expect(neu).toHaveBeenCalledTimes(1);
    window.removeEventListener("nodvard-deck:navigate", neu);
  });

  it("die Seite reagiert je Link einmal", async () => {
    const shell = { React: undefined as never, getAccessToken: () => null, hasPermission: () => true } as unknown as NodvardDeckShell;
    installDeckGlobal(shell);
    renderApp();
    await screen.findByTestId("query");
    syncs = 0;

    fireEvent.click(screen.getByRole("link", { name: "Link Guard" }));
    await vi.waitFor(() => expect(screen.getByTestId("query")).toHaveTextContent("?tab=guard"));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(syncs).toBe(1);
    fireEvent.click(screen.getByRole("link", { name: "Link ohne Query" }));
    await vi.waitFor(() => expect(syncs).toBe(2));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(syncs).toBe(2);
    expect(screen.getByTestId("query").textContent).toBe("");
  });

  it("erreicht die Seite auch, wenn der Router dieselbe Adresse noch für aktuell hält (Seite hat sie per replaceState geändert)", async () => {
    renderApp();
    await screen.findByTestId("query");
    fireEvent.click(screen.getByRole("link", { name: "Link Guard" }));
    await vi.waitFor(() => expect(screen.getByTestId("query")).toHaveTextContent("?tab=guard"));
    const mountsBefore = mounts;

    // Die Seite stellt im Reiterstreifen um und schreibt es zurück -- der Router erfährt davon nichts.
    fireEvent.click(screen.getByRole("button", { name: "Reiter Updates" }));
    expect(screen.getByTestId("query")).toHaveTextContent("?tab=updates");
    // Derselbe Link erneut: für den Router ändert sich die Query nicht (kein Neuaufbau über key),
    // die Seite muss es trotzdem über das Ereignis erfahren.
    fireEvent.click(screen.getByRole("link", { name: "Link Guard" }));
    await vi.waitFor(() => expect(screen.getByTestId("query")).toHaveTextContent("?tab=guard"));
    expect(mounts).toBe(mountsBefore);
  });

  it("setzt navigateEvents, solange sie eingehängt ist (die Seiten hören dann nicht selbst auf popstate)", async () => {
    installDeckGlobal({} as NodvardDeckShell);
    const router = createBrowserRouter([{ path: "/ext/:extId/*", element: <ExtensionPage /> }]);
    const { unmount } = render(<RouterProvider router={router} />);
    await screen.findByTestId("query");
    expect(window.__nodvardDeck.navigateEvents).toBe(true);
    unmount();
    expect(window.__nodvardDeck.navigateEvents).toBe(false);
  });

  it("baut die Seite bei geänderter Query weiter neu auf", async () => {
    renderApp();
    await screen.findByTestId("query");
    const mountsBefore = mounts;
    fireEvent.click(screen.getByRole("link", { name: "Link Guard" }));
    await vi.waitFor(() => expect(mounts).toBeGreaterThan(mountsBefore));
  });
});
