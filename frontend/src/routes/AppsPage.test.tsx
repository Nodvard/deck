import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { moveBefore, moveToEndOf } from "../lib/apps";
import type { AppTileOut } from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { AppsPage, foldersOf } from "./AppsPage";

vi.mock("../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

interface Call { method: string; url: string; body: Record<string, unknown> | null }
let calls: Call[];

const custom = (id: string, name: string, over: Partial<AppTileOut> = {}): AppTileOut => ({
  id, source: "custom", name, url: `https://${id}.example.org`, host: null, host_id: null, state: null, tone: null, image: null,
  icon: null, color: null, group: null, open_in_new_tab: true, sort_order: 0, ...over,
});

const APPS: AppTileOut[] = [
  custom("a1", "Router", { group: "Netzwerk", sort_order: 0 }),
  custom("a2", "Pi-hole", { group: "Netzwerk", sort_order: 1 }),
  custom("a3", "NAS", { group: "Speicher", sort_order: 2 }),
  custom("a4", "Drucker", { sort_order: 3 }),
  {
    id: "d1", source: "detected", name: "grafana", url: "http://10.0.0.5:3000", host: "docker", host_id: "h1", state: "running",
    tone: "good", image: "grafana", icon: null, color: null, group: null, open_in_new_tab: true, sort_order: null,
  },
];
const SUGGESTIONS = {
  suggestions: [
    { name: "cloud.example.org", url: "https://cloud.example.org", source: "Nginx Proxy Manager", host: null },
    { name: "uptime", url: "http://10.0.0.5:3001", source: "Docker", host: "docker" },
  ],
  errors: ["Vorschläge von „Netzwerk“ ließen sich gerade nicht lesen."],
};

function installFetch() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace("/api/v1", "");
    calls.push({ method: init?.method ?? "GET", url, body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
    if (url.startsWith("/overview")) return new Response(JSON.stringify({ apps: APPS, services: [], services_running: 0, backups: null, pending_actions: 0, unread_notifications: 0, attention: [], errors: [], generated_at: 0 }), { status: 200 });
    if (url.startsWith("/hosts")) return new Response("[]", { status: 200 });
    if (url === "/apps/suggestions") return new Response(JSON.stringify(SUGGESTIONS), { status: 200 });
    if (url.startsWith("/apps")) return new Response(JSON.stringify({ id: "neu" }), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
}

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><AppsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Ein Ziehvorgang mit einem Datenobjekt, wie jsdom es nicht von selbst baut. */
function drag(from: HTMLElement, to: HTMLElement) {
  const dataTransfer = { data: {} as Record<string, string>, setData(k: string, v: string) { this.data[k] = v; }, getData(k: string) { return this.data[k]; }, effectAllowed: "", dropEffect: "" };
  fireEvent.dragStart(from, { dataTransfer });
  fireEvent.dragOver(to, { dataTransfer });
  fireEvent.drop(to, { dataTransfer });
  fireEvent.dragEnd(from, { dataTransfer });
}

beforeEach(() => {
  login(["apps.write", "hosts.read"]);
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("Seite „Apps“", () => {
  it("zeigt eigene Apps in Ordnern, „Ohne Ordner“ zuerst, und darunter die erkannten Dienste", async () => {
    renderPage();
    const page = await screen.findByTestId("apps-page");
    await screen.findByTestId("folder-Netzwerk");
    const folders = within(page).getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(folders.slice(0, 3)).toEqual(["Ordner Ohne Ordner", "Ordner Netzwerk", "Ordner Speicher"]);
    expect(within(screen.getByTestId("folder-Netzwerk")).getAllByTestId(/^app-custom-/).map((el) => el.dataset.testid)).toEqual([
      "app-custom-a1", "app-custom-a2",
    ]);
    expect(within(screen.getByTestId("detected")).getByText("grafana")).toBeInTheDocument();
  });

  it("klappt einen Ordner zu und wieder auf", async () => {
    renderPage();
    const button = within(await screen.findByTestId("folder-Netzwerk")).getByRole("button", { name: /Netzwerk/ });
    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(within(screen.getByTestId("folder-Netzwerk")).queryByText("Router")).toBeNull();
  });

  it("sortiert per Ziehen auf eine Kachel im selben Ordner um", async () => {
    renderPage();
    await screen.findByTestId("folder-Netzwerk");
    drag(screen.getByTestId("drag-a2"), screen.getByTestId("drag-a1"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/apps/order")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ ids: ["a2", "a1", "a3", "a4"] });
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("legt eine Kachel per Ziehen auf die Überschrift in einen anderen Ordner", async () => {
    renderPage();
    const folder = await screen.findByTestId("folder-Speicher");
    drag(screen.getByTestId("drag-a4"), within(folder).getByRole("heading"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/apps/a4");
    expect(patch?.body?.group).toBe("Speicher");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ ids: ["a1", "a2", "a3", "a4"] });
  });

  it("zeigt Vorschläge mit Quelle; „Übernehmen“ füllt den Dialog vor", async () => {
    renderPage();
    const list = await screen.findByTestId("suggestions");
    await within(list).findByText("cloud.example.org");
    expect(within(list).getByText(/Nginx Proxy Manager/)).toBeInTheDocument();
    expect(within(list).getByText(/ließen sich gerade nicht lesen/)).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("button", { name: "cloud.example.org übernehmen" }));
    expect(await screen.findByDisplayValue("https://cloud.example.org")).toBeInTheDocument();
    expect(screen.getByDisplayValue("cloud.example.org")).toBeInTheDocument();
  });

  it("ohne Schreibrecht: kein Ziehen, keine Vorschläge, kein Hinzufügen", async () => {
    login(["hosts.read"]);
    renderPage();
    await screen.findByTestId("folder-Netzwerk");
    expect(screen.getByTestId("drag-a1")).not.toHaveAttribute("draggable", "true");
    expect(screen.queryByTestId("suggestions")).toBeNull();
    expect(screen.queryByRole("button", { name: /App hinzufügen/ })).toBeNull();
    expect(calls.some((c) => c.url === "/apps/suggestions")).toBe(false);
  });

  it("während einer Suche wird nicht gezogen", async () => {
    renderPage();
    await screen.findByTestId("folder-Netzwerk");
    fireEvent.change(screen.getByLabelText("Apps suchen"), { target: { value: "rou" } });
    expect(screen.getByTestId("drag-a1")).not.toHaveAttribute("draggable", "true");
    expect(screen.getByText("Zum Sortieren erst die Suche leeren.")).toBeInTheDocument();
  });
});

describe("Hilfen", () => {
  it("moveBefore und moveToEndOf", () => {
    expect(moveBefore(["a", "b", "c"], "c", "a")).toEqual(["c", "a", "b"]);
    expect(moveBefore(["a", "b", "c"], "a", "a")).toEqual(["a", "b", "c"]);
    expect(moveBefore(["a", "b"], "x", "a")).toEqual(["a", "b"]);
    expect(moveToEndOf(["a", "b", "c", "d"], "a", ["b", "c"])).toEqual(["b", "c", "a", "d"]);
    expect(moveToEndOf(["a", "b"], "a", [])).toEqual(["b", "a"]);
  });

  it("foldersOf ordnet „Ohne Ordner“ zuerst, dann nach Name, innen nach Position", () => {
    const folders = foldersOf([custom("x", "X", { group: "b", sort_order: 2 }), custom("y", "Y", { group: "a", sort_order: 1 }), custom("z", "Z", { sort_order: 5 })]);
    expect(folders.map((f) => f.label)).toEqual(["Ohne Ordner", "a", "b"]);
  });
});

describe("Ladefehler", () => {
  it("Übersicht kommt nicht an: der Grund statt „Noch keine Apps“, „Erneut versuchen“ lädt nach", async () => {
    let fail = true;
    const ok = (globalThis.fetch as unknown as (i: RequestInfo | URL, init?: RequestInit) => Promise<Response>);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (fail && String(input).includes("/overview")) return new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status: 500 });
      return ok(input, init);
    }));
    renderPage();
    const error = await screen.findByTestId("load-error");
    expect(error.textContent).toContain("Die Liste der Apps ließ sich gerade nicht laden: Datenbank gesperrt");
    expect(screen.queryByTestId("apps-page-empty")).toBeNull();
    fail = false;
    fireEvent.click(within(error).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("folder-Netzwerk")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });
});
