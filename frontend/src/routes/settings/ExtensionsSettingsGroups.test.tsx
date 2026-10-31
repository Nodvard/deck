import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { useAuthStore } from "../../state/auth";
import { ExtensionsSettings } from "./ExtensionsSettings";

/**
 * Die Modul-Liste unter Einstellungen → Erweiterungen hat dieselben Gruppen wie der Einrichtungs-Assistent.
 * „Hello World“ steht dort nicht versteckt, sondern in der letzten Gruppe „Für Entwickler“.
 */
const base = { version: "1.0.0", source: "bundled", description: null, icon: null, granted_permissions: [], last_error: null };
let rows: Record<string, unknown>[];
let switchedOff: string[];

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
  switchedOff = [];
  rows = [
    { ...base, id: "hello-world", name: "Hello World", state: "enabled", category: "example", sort_order: 10 },
    { ...base, id: "scripts", name: "Skripte", state: "enabled", category: "tools", sort_order: 10 },
    { ...base, id: "ntfy", name: "Push-Nachrichten", state: "disabled", category: "connections", sort_order: 10 },
    { ...base, id: "proxmox", name: "Proxmox VE", state: "enabled", category: "servers", sort_order: 40 },
    { ...base, id: "system", name: "System", state: "enabled", category: "servers", sort_order: 10 },
    { ...base, id: "shield", name: "Nodvard Shield", state: "disabled", category: "security", sort_order: 10 },
    { ...base, id: "fremd", name: "Fremdes Modul", state: "disabled" },
  ];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    if (method === "GET" && path === "/extensions") return new Response(JSON.stringify(rows));
    if (method === "GET" && path === "/hosts") return new Response("[]");
    const off = path.match(/^\/extensions\/([^/]+)\/disable$/);
    if (method === "POST" && off) {
      switchedOff.push(off[1]);
      rows = rows.map((r) => (r.id === off[1] ? { ...r, state: "disabled" } : r));
      return new Response(JSON.stringify({ id: off[1], state: "disabled" }));
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderList() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><ExtensionsSettings /><GlobalDialogs /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Einstellungen → Erweiterungen: Gruppen", () => {
  it("zeigt dieselben Gruppen wie der Assistent, Hello World zuletzt unter „Für Entwickler“", async () => {
    renderList();
    await screen.findByTestId("ext-system");
    const regions = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(regions).toEqual(["Für deine Server", "Sicherheit", "Werkzeuge", "Verbindungen zu anderen Diensten", "Weitere Module", "Für Entwickler"]);

    const inGroup = (id: string) => within(screen.getByTestId(`ext-group-${id}`)).getAllByRole("listitem").map((li) => li.getAttribute("data-testid"));
    // Reihenfolge innerhalb der Gruppe: nach `sort_order` aus dem Manifest, nicht mehr nach dem Alphabet
    expect(inGroup("servers")).toEqual(["ext-system", "ext-proxmox"]);
    expect(inGroup("other")).toEqual(["ext-fremd"]);
    expect(inGroup("example")).toEqual(["ext-hello-world"]);
    expect(within(screen.getByTestId("ext-group-example")).getByText(/Du brauchst sie nicht/)).toBeInTheDocument();
  });

  it("Hello World ist nicht versteckt: ein eingeschaltetes Beispiel-Modul lässt sich ausschalten", async () => {
    renderList();
    fireEvent.click(await screen.findByRole("switch", { name: "Hello World ausschalten" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
    await waitFor(() => expect(switchedOff).toEqual(["hello-world"]));
    await screen.findByText("„Hello World“ ausgeschaltet.");
  });

  it("ein beim Start abgestürztes Modul hat „Neu starten“ und „Ausschalten“ statt eines Schalters, der Grund steht auf Deutsch", async () => {
    rows = rows.map((r) => (r.id === "proxmox"
      ? { ...r, state: "error", last_error: "on_start() fehlgeschlagen: Verbindung abgelehnt" }
      : r));
    renderList();
    const row = await screen.findByTestId("ext-proxmox");
    expect(within(row).getByText("Beim Starten des Moduls ist ein Fehler aufgetreten: Verbindung abgelehnt")).toBeInTheDocument();
    expect(within(row).queryByText(/on_start\(\)/)).not.toBeInTheDocument();
    expect(within(row).queryByRole("switch")).not.toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Proxmox VE neu starten" })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Proxmox VE ausschalten" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
    await waitFor(() => expect(switchedOff).toEqual(["proxmox"]));
  });

  it("ohne Beispiel-Modul gibt es die Gruppe „Für Entwickler“ nicht", async () => {
    rows = rows.filter((r) => r.id !== "hello-world");
    renderList();
    await screen.findByTestId("ext-system");
    expect(screen.queryByText("Für Entwickler")).toBeNull();
  });
});
