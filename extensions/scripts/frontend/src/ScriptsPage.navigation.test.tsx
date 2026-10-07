/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> ScriptsPage: der
 * Server-Filter folgt Links aus der Server-Seite auch dann, wenn die Seite schon offen ist und
 * man ihn zwischendurch entfernt hat. Vorbild: extensions/shield SocPage.navigation.test.tsx.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderInShell } from "../../../_shared/frontend/src/testShell";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "scripts", path: "/scripts", component: "ScriptsPage" }], isLoading: false }),
}));
// ExtensionPage laedt das Bundle per import(url) -- hier durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/scripts/frontend/index.js?v=dev", async () => ({ ScriptsPage: (await import("./ScriptsPage")).ScriptsPage }));

const SCRIPT = { description: "", content: "uptime\n", params_schema: {}, schedule: null, enabled: true };
const SCRIPTS = [
  { ...SCRIPT, id: "uptime", name: "Uptime", target: { kind: "host", host_id: "h-pi" }, job_id: "script-uptime" },
  { ...SCRIPT, id: "aufraeumen", name: "Aufräumen", target: { kind: "host", host_id: "h-docker" }, job_id: "script-aufraeumen" },
];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/ext/scripts/scripts")) return json(SCRIPTS);
    if (url.includes("/jobs")) return json([]);
    if (url.endsWith("/host-groups")) return json([]);
    if (url.endsWith("/hosts")) return json([{ id: "h-pi", name: "pi", display_name: "Raspberry Pi" }, { id: "h-docker", name: "docker", display_name: "docker" }]);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

const LINKS = { "Menü Skripte": "/ext/scripts/scripts", "Skripte Raspberry Pi": "/ext/scripts/scripts?host=h-pi" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
// Beides zusammen abwarten: bei Zurück/Vor folgt die Seite erst selbst (popstate), dann baut der
// Kern sie wegen der geänderten Query neu auf -- ein einmal gefundenes Element kann dabei verschwinden.
const filtered = () => waitFor(() => {
  expect(screen.getByTestId("host-filter")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Uptime/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Aufräumen/ })).toBeNull();
});
const unfiltered = () => waitFor(() => {
  expect(screen.getByRole("button", { name: /Aufräumen/ })).toBeInTheDocument();
  expect(screen.queryByTestId("host-filter")).toBeNull();
});

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/scripts/scripts");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("Skripte in der echten Kern-Shell", () => {
  it("Link, Filter entfernen, derselbe Link erneut: der Filter ist wieder da", async () => {
    renderInShell(LINKS);
    await unfiltered();

    click("Skripte Raspberry Pi");
    await filtered();

    fireEvent.click(within(screen.getByTestId("host-filter")).getByRole("button", { name: "Filter entfernen" }));
    await unfiltered();
    expect(window.location.search).toBe("");

    // Der Router hält ?host=h-pi noch für die aktuelle Adresse -- die Seite muss trotzdem folgen.
    click("Skripte Raspberry Pi");
    await filtered();
    expect(window.location.search).toBe("?host=h-pi");
  });

  it("Zurück und Vor folgen", async () => {
    renderInShell(LINKS);
    await unfiltered();
    click("Skripte Raspberry Pi");
    await filtered();
    click("Menü Skripte");
    await unfiltered();

    act(() => window.history.back());
    await filtered();
    act(() => window.history.forward());
    await unfiltered();
  });
});
