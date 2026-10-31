/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> SystemPage: der gewählte
 * Server folgt Links aus der Server-Seite auch dann, wenn die Seite schon offen ist und man
 * zwischendurch umgeschaltet hat. Vorbild: extensions/shield SocPage.navigation.test.tsx.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderInShell } from "../../../_shared/frontend/src/testShell";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "system", path: "/system", component: "SystemPage" }], isLoading: false }),
}));
// ExtensionPage lädt das Bundle per import(url); hier wird es durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/system/frontend/index.js?v=dev", async () => ({ SystemPage: (await import("./SystemPage")).SystemPage }));

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/api/v1/hosts")) {
      return json([
        { id: "h-pi", display_name: "Raspberry Pi", address: "192.168.1.72", os_family: "linux", status: "up" },
        { id: "h-docker", display_name: "docker", address: "192.168.1.85", os_family: "linux", status: "up" },
      ]);
    }
    // Live-Werte sind hier egal -- ein Fehler reicht, die Seite zeigt ihn nur an.
    if (url.includes("/live")) return new Response(JSON.stringify({ detail: "egal" }), { status: 500 });
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

const LINKS = { "Menü System": "/ext/system/system", "System Raspberry Pi": "/ext/system/system?host=h-pi" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
const shows = (title: string) => waitFor(() => expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(title));

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/system/system");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
});

describe("Server-Zustand in der echten Kern-Shell", () => {
  it("Link, anderen Server wählen, derselbe Link erneut: wieder der Server aus dem Link", async () => {
    renderInShell(LINKS);
    await screen.findByTestId("host-picker");

    click("System Raspberry Pi");
    await shows("Server-Zustand: Raspberry Pi");

    fireEvent.click(screen.getByRole("button", { name: "Server wechseln" }));
    fireEvent.click(within(screen.getByTestId("host-picker")).getByRole("button", { name: /docker/ }));
    await shows("Server-Zustand: docker");
    expect(window.location.search).toBe("?host=h-docker");

    // Der Router hält ?host=h-pi noch für die aktuelle Adresse -- die Seite muss trotzdem folgen.
    click("System Raspberry Pi");
    await shows("Server-Zustand: Raspberry Pi");
    expect(window.location.search).toBe("?host=h-pi");
  });

  it("Zurück und Vor folgen", async () => {
    renderInShell(LINKS);
    await screen.findByTestId("host-picker");
    click("System Raspberry Pi");
    await shows("Server-Zustand: Raspberry Pi");
    click("Menü System");
    await shows("Server-Zustand");

    act(() => window.history.back());
    await shows("Server-Zustand: Raspberry Pi");
    act(() => window.history.forward());
    await shows("Server-Zustand");
  });

  // Zurück/Vor meldet die Kern-Shell selbst (nodvard-deck:navigate); hörte die Seite zusätzlich auf popstate, fragte sie
  // den Server doppelt ab.
  it("Zurück und Vor fragen die Live-Werte nur einmal ab", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    const liveCalls = () => fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/hosts/h-pi/live")).length;
    renderInShell(LINKS);
    await screen.findByTestId("host-picker");
    click("System Raspberry Pi");
    await shows("Server-Zustand: Raspberry Pi");
    await waitFor(() => expect(liveCalls()).toBe(1));
    click("Menü System");
    await shows("Server-Zustand");

    const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    act(() => window.history.back());
    await shows("Server-Zustand: Raspberry Pi");
    await settle();
    expect(liveCalls()).toBe(2);

    act(() => window.history.forward());
    await shows("Server-Zustand");
    await settle();
    expect(liveCalls()).toBe(2);

    act(() => window.history.back());
    await shows("Server-Zustand: Raspberry Pi");
    await settle();
    expect(liveCalls()).toBe(3);
  });
});
