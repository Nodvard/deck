/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> BackupsPage: der
 * Server-Filter folgt Links aus der Server-Seite auch dann, wenn die Seite schon offen ist und
 * man ihn zwischendurch entfernt hat. Vorbild: nexus-soc SocPage.navigation.test.tsx.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderInShell } from "../../../_shared/frontend/src/testShell";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "backups", path: "/backups", component: "BackupsPage" }], isLoading: false }),
}));
// ExtensionPage laedt das Bundle per import(url) -- hier durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/backups/frontend/index.js?v=dev", async () => ({ BackupsPage: (await import("./BackupsPage")).BackupsPage }));

const JOB = { connection: "pve2", node: "pve2", storage: "backup-pve1", schedule: "0 2 * * *", enabled: true, last_status: "ok", last_run_at: 1700000000 };
const JOBS = [
  { ...JOB, job_ref: "pve2--job1--100", vmid: "100", name: "docker", host_id: "h1" },
  { ...JOB, job_ref: "pve2--job1--101", vmid: "101", name: "nextcloud", host_id: "h2" },
];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/ext/backups/connections")) return json([]);
    if (url.endsWith("/ext/backups/inventory") || url.endsWith("/ext/backups/unprotected")) return json({ guests: [], errors: [] });
    if (url.endsWith("/ext/backups/jobs")) return json(JOBS);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

const LINKS = { "Menü Backups": "/ext/backups/backups", "Backups docker": "/ext/backups/backups?host=h1" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
// Beides zusammen abwarten: bei Zurück/Vor folgt die Seite erst selbst (popstate), dann baut der
// Kern sie wegen der geänderten Query neu auf -- ein einmal gefundenes Element kann dabei verschwinden.
const filtered = () => waitFor(() => {
  expect(screen.getByTestId("host-filter")).toBeInTheDocument();
  expect(screen.queryByText("(101)")).toBeNull();
});
const unfiltered = () => waitFor(() => {
  expect(screen.getByText("(101)")).toBeInTheDocument();
  expect(screen.queryByTestId("host-filter")).toBeNull();
});

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/backups/backups");
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

describe("Backups in der echten Kern-Shell", () => {
  it("Link, Filter entfernen, derselbe Link erneut: der Filter ist wieder da", async () => {
    renderInShell(LINKS);
    await unfiltered();

    click("Backups docker");
    await filtered();

    fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
    await unfiltered();
    expect(window.location.search).toBe("");

    // Der Router hält ?host=h1 noch für die aktuelle Adresse -- die Seite muss trotzdem folgen.
    click("Backups docker");
    await filtered();
    expect(window.location.search).toBe("?host=h1");
  });

  it("Zurück und Vor folgen", async () => {
    renderInShell(LINKS);
    await unfiltered();
    click("Backups docker");
    await filtered();
    click("Menü Backups");
    await unfiltered();

    act(() => window.history.back());
    await filtered();
    act(() => window.history.forward());
    await unfiltered();
  });
});
