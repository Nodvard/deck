/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> useUrlParams (Kit): das
 * Kit reagiert auf eine Navigation genau einmal -- im neuen Kern (beide Namen, beide Ereignisse),
 * mit neuem Kit an einem alten Kern (nur `__lattice`) und auch mit einem alten Kit (hoert nur auf
 * `lattice:navigate`) an einem neuen Kern.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installDeckGlobal } from "../../../../frontend/src/lib/deckGlobal";
import { useUrlParams } from "./location";
import { renderInShell } from "./testShell";

let renderedVisits = -1;
let oldKitHeard = 0;

/** Seite mit dem Hook des neuen Kits. */
function KitPage() {
  const [params, , visits] = useUrlParams();
  renderedVisits = visits;
  return (
    <>
      <p data-testid="tab">{params.get("tab") ?? "-"}</p>
      <p data-testid="visits">{visits}</p>
    </>
  );
}

/** Seite mit dem Verhalten des ALTEN Kits: hoert nur auf `lattice:navigate`. */
function OldKitPage() {
  const [search, setSearch] = useState(() => window.location.search);
  useEffect(() => {
    const sync = () => {
      oldKitHeard += 1;
      setSearch(window.location.search);
    };
    window.addEventListener("lattice:navigate", sync);
    return () => window.removeEventListener("lattice:navigate", sync);
  }, []);
  return <p data-testid="old-query">{search}</p>;
}

let currentPage: "KitPage" | "OldKitPage" = "KitPage";
vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "demo", path: "/page", component: currentPage }], isLoading: false }),
}));
vi.mock("/api/v1/extensions/demo/frontend/index.js?v=dev", () => ({ KitPage, OldKitPage }));

const LINKS = { "Gleiche Adresse": "/ext/demo/page", "Reiter Guard": "/ext/demo/page?tab=guard" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

const shell = { React: undefined as never, getAccessToken: () => "tok", hasPermission: () => true } as unknown as NodvardDeckShell;

beforeEach(() => {
  renderedVisits = -1;
  oldKitHeard = 0;
  currentPage = "KitPage";
  window.history.replaceState(null, "", "/ext/demo/page");
});
afterEach(() => {
  delete (window as Partial<Window>).__nodvardDeck;
  delete (window as Partial<Window>).__lattice;
});

describe("Navigation in der Erweiterungsseite: genau eine Reaktion", () => {
  it("neuer Kern (beide Namen): jeder Link zaehlt einmal", async () => {
    installDeckGlobal(shell);
    renderInShell(LINKS);
    await screen.findByTestId("visits");
    expect(renderedVisits).toBe(0);

    click("Gleiche Adresse"); // keine neue Query: die Seite bleibt, nur das Ereignis erreicht sie
    await waitFor(() => expect(screen.getByTestId("visits")).toHaveTextContent("1"));
    await settle();
    expect(screen.getByTestId("visits")).toHaveTextContent("1"); // nicht 2

    click("Gleiche Adresse");
    await waitFor(() => expect(screen.getByTestId("visits")).toHaveTextContent("2"));
    await settle();
    expect(screen.getByTestId("visits")).toHaveTextContent("2");
  });

  it("neues Kit mit altem Kern (nur __lattice): ebenfalls einmal je Link", async () => {
    window.__lattice = shell;
    renderInShell(LINKS);
    await screen.findByTestId("visits");

    click("Gleiche Adresse");
    await waitFor(() => expect(screen.getByTestId("visits")).toHaveTextContent("1"));
    await settle();
    expect(screen.getByTestId("visits")).toHaveTextContent("1");
  });

  it("neuer Kern, die Seite folgt der Query (Reiter) und baut sich dabei einmal neu auf", async () => {
    installDeckGlobal(shell);
    renderInShell(LINKS);
    await screen.findByTestId("tab");
    click("Reiter Guard");
    await waitFor(() => expect(screen.getByTestId("tab")).toHaveTextContent("guard"));
    // ExtensionPage baut die Seite bei geaenderter Query neu auf: frischer Zaehler, genau eine Reaktion.
    await settle();
    expect(screen.getByTestId("visits")).toHaveTextContent("1");
  });

  it("neuer Kern, aelteres Kit (hoert nur auf lattice:navigate): erfaehrt weiterhin von jedem Link, einmal", async () => {
    installDeckGlobal(shell);
    currentPage = "OldKitPage";
    renderInShell(LINKS);
    await screen.findByTestId("old-query");
    oldKitHeard = 0;

    click("Gleiche Adresse");
    await waitFor(() => expect(oldKitHeard).toBe(1));
    await settle();
    expect(oldKitHeard).toBe(1);
  });
});
