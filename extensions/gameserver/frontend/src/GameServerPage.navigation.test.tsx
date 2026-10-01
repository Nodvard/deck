/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> GameServerPage: ein Link
 * von der Server-Seite stellt den Server nach vorn und scrollt hin -- auch dann, wenn die Seite
 * schon offen ist und derselbe Link noch einmal kommt. Vorbild: nexus-soc SocPage.navigation.test.tsx.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderInShell } from "../../../_shared/frontend/src/testShell";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "gameserver", path: "/gameservers", component: "GameServerPage" }], isLoading: false }),
}));
// ExtensionPage laedt das Bundle per import(url) -- hier durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/gameserver/frontend/index.js?v=dev", async () => ({ GameServerPage: (await import("./GameServerPage")).GameServerPage }));

const SERVER = { status: "up", service_label: "läuft", tone: "good", running: true, players_online: 0, version: "1.0", error: null,
  can_start: false, can_stop: true, join_code: null, join_code_display: null, details: {}, config: { profile: "", values: {} } };
const SERVERS = [{ ...SERVER, host_id: "h1", name: "game-win" }, { ...SERVER, host_id: "h2", name: "minecraft" }];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/ext/gameserver/servers")) return json(SERVERS);
    const one = /\/ext\/gameserver\/servers\/(h\d)$/.exec(url);
    if (one) return json(SERVERS.find((s) => s.host_id === one[1]));
    if (url.endsWith("/ext/gameserver/profiles")) return json([]);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

const LINKS = { "Menü Gameserver": "/ext/gameserver/gameservers", "Gameserver minecraft": "/ext/gameserver/gameservers?host=h2" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
const order = () => screen.getAllByTestId(/^server-/).map((e) => e.dataset.testid);

let scrolled: string[];
const original = Element.prototype.scrollIntoView;

beforeEach(() => {
  scrolled = [];
  Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this.id); };
  window.history.replaceState(null, "", "/ext/gameserver/gameservers");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    // Betrachter: Details ohne Log von der einfachen Route, keine Knöpfe.
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(false),
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  vi.unstubAllGlobals();
  Element.prototype.scrollIntoView = original;
  window.history.replaceState(null, "", "/");
});

describe("Gameserver in der echten Kern-Shell", () => {
  it("Link stellt den Server nach vorn und scrollt hin; derselbe Link erneut scrollt wieder", async () => {
    renderInShell(LINKS);
    await waitFor(() => expect(order()).toEqual(["server-h1", "server-h2"]));
    expect(scrolled).toEqual([]);

    click("Gameserver minecraft");
    await waitFor(() => expect(order()).toEqual(["server-h2", "server-h1"]));
    await waitFor(() => expect(scrolled).toEqual(["host-h2"]));

    // Inzwischen weggescrollt: der Router hält die Adresse für unverändert, die Seite scrollt trotzdem.
    click("Gameserver minecraft");
    await waitFor(() => expect(scrolled).toEqual(["host-h2", "host-h2"]));

    click("Menü Gameserver");
    await waitFor(() => expect(order()).toEqual(["server-h1", "server-h2"]));
    expect(scrolled).toHaveLength(2);
  });

  it("Zurück und Vor folgen", async () => {
    renderInShell(LINKS);
    await waitFor(() => expect(order()).toEqual(["server-h1", "server-h2"]));
    click("Gameserver minecraft");
    await waitFor(() => expect(order()).toEqual(["server-h2", "server-h1"]));
    click("Menü Gameserver");
    await waitFor(() => expect(order()).toEqual(["server-h1", "server-h2"]));

    act(() => window.history.back());
    await waitFor(() => expect(order()).toEqual(["server-h2", "server-h1"]));
    act(() => window.history.forward());
    await waitFor(() => expect(order()).toEqual(["server-h1", "server-h2"]));
  });
});
