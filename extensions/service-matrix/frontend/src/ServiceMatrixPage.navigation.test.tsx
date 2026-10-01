/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> ServiceMatrixPage: der
 * Server-Filter folgt Links aus der Server-Seite auch dann, wenn die Seite schon offen ist und
 * man ihn zwischendurch entfernt hat. Vorbild: nexus-soc SocPage.navigation.test.tsx.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderInShell } from "../../../_shared/frontend/src/testShell";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "service-matrix", path: "/matrix", component: "ServiceMatrixPage" }], isLoading: false }),
}));
// ExtensionPage laedt das Bundle per import(url) -- hier durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/service-matrix/frontend/index.js?v=dev", async () => ({
  ServiceMatrixPage: (await import("./ServiceMatrixPage")).ServiceMatrixPage,
}));

const SERVICES = [
  { id: "h1:nginx", name: "nginx", host: "docker", host_id: "h1", container: "nginx", is_self: false, state: "running", status: "Up 2 hours", tone: "good", url: null },
  { id: "h2:redis", name: "redis", host: "docker-lxc", host_id: "h2", container: "redis", is_self: false, state: "running", status: "Up 1 day", tone: "good", url: null },
];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/ext/service-matrix/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }), { status: 200 });
    if (url.includes("/ext/service-matrix/stats")) return new Response(JSON.stringify({ data: {} }), { status: 200 });
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

const LINKS = { "Menü Service-Matrix": "/ext/service-matrix/matrix", "Container docker": "/ext/service-matrix/matrix?host=h1" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
// Beides zusammen abwarten: bei Zurück/Vor folgt die Seite erst selbst (popstate), dann baut der
// Kern sie wegen der geänderten Query neu auf -- ein einmal gefundenes Element kann dabei verschwinden.
const filtered = () => waitFor(() => {
  expect(screen.getByTestId("host-filter")).toBeInTheDocument();
  expect(screen.getByText("nginx")).toBeInTheDocument();
  expect(screen.queryByText("redis")).toBeNull();
});
const unfiltered = () => waitFor(() => {
  expect(screen.getByText("redis")).toBeInTheDocument();
  expect(screen.queryByTestId("host-filter")).toBeNull();
});

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/service-matrix/matrix");
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

describe("Service-Matrix in der echten Kern-Shell", () => {
  it("Link, Filter entfernen, derselbe Link erneut: der Filter ist wieder da", async () => {
    renderInShell(LINKS);
    await unfiltered();

    click("Container docker");
    await filtered();

    fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
    await unfiltered();
    expect(window.location.search).toBe("");

    // Der Router hält ?host=h1 noch für die aktuelle Adresse -- die Seite muss trotzdem folgen.
    click("Container docker");
    await filtered();
    expect(window.location.search).toBe("?host=h1");
  });

  it("Zurück und Vor folgen", async () => {
    renderInShell(LINKS);
    await unfiltered();
    click("Container docker");
    await filtered();
    click("Menü Service-Matrix");
    await unfiltered();

    act(() => window.history.back());
    await filtered();
    act(() => window.history.forward());
    await unfiltered();
  });
});
