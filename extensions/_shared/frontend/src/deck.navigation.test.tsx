/** Ende-zu-Ende durch Router -> ExtensionPage (Kern) -> useUrlParams (Kit): das Kit reagiert auf eine Navigation genau
 * einmal. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installDeckGlobal } from "../../../../frontend/src/lib/deckGlobal";
import { useUrlParams } from "./location";
import { renderInShell } from "./testShell";

let renderedVisits = -1;

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

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "demo", path: "/page", component: "KitPage" }], isLoading: false }),
}));
vi.mock("/api/v1/extensions/demo/frontend/index.js?v=dev", () => ({ KitPage }));

const LINKS = { "Gleiche Adresse": "/ext/demo/page", "Reiter Guard": "/ext/demo/page?tab=guard" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

const shell = { React: undefined as never, getAccessToken: () => "tok", hasPermission: () => true } as unknown as NodvardDeckShell;

beforeEach(() => {
  renderedVisits = -1;
  window.history.replaceState(null, "", "/ext/demo/page");
});
afterEach(() => {
  delete (window as Partial<Window>).__nodvardDeck;
});

describe("Navigation in der Erweiterungsseite: genau eine Reaktion", () => {
  it("jeder Link zählt einmal", async () => {
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

  it("die Seite folgt der Query (Reiter) und baut sich dabei einmal neu auf", async () => {
    installDeckGlobal(shell);
    renderInShell(LINKS);
    await screen.findByTestId("tab");
    click("Reiter Guard");
    await waitFor(() => expect(screen.getByTestId("tab")).toHaveTextContent("guard"));
    // ExtensionPage baut die Seite bei geaenderter Query neu auf: frischer Zaehler, genau eine Reaktion.
    await settle();
    expect(screen.getByTestId("visits")).toHaveTextContent("1");
  });
});
