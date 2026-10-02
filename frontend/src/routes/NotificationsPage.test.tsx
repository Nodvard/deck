import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { NotificationsPage } from "./NotificationsPage";

vi.mock("../lib/ws", () => ({ useWsSubscription: () => undefined }));

const LONG_BODY = Array.from({ length: 8 }, (_, i) => `Zeile ${i + 1} des Lageberichts`).join("\n");

let rows: { id: string; ts: string; severity: string; title: string; body: string; source_ext_id: string | null; correlation_id: null; read_at: string | null; payload?: Record<string, unknown> }[];

function mockFetch(calls: { url: string; method: string; body?: unknown }[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes("/api/v1/notifications?") && method === "GET") {
      const unreadOnly = url.includes("unread=true");
      return new Response(JSON.stringify(rows.filter((r) => !unreadOnly || !r.read_at)));
    }
    if (url.endsWith("/api/v1/notifications/read") && method === "POST") {
      const ids = (JSON.parse(init!.body as string) as { ids: string[] }).ids;
      rows = rows.map((r) => (ids.includes(r.id) ? { ...r, read_at: "2026-09-24T12:00:00Z" } : r));
      return new Response(JSON.stringify({ marked: ids.length }));
    }
    if (url.endsWith("/api/v1/notifications/read-all") && method === "POST") {
      rows = rows.map((r) => ({ ...r, read_at: r.read_at ?? "2026-09-24T12:00:00Z" }));
      return new Response(JSON.stringify({ marked: 2 }));
    }
    if (url.endsWith("/api/v1/extensions") && method === "GET") {
      return new Response(JSON.stringify([
        { id: "nexus-soc", name: "Nodvard Shield", state: "enabled" },
        { id: "proxmox", name: "Proxmox VE", state: "enabled" },
      ]));
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NotificationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  rows = [
    { id: "n1", ts: "2026-09-24T11:00:00Z", severity: "warning", title: "Lagebericht der Container-Wache (2 Ereignisse)", body: LONG_BODY, source_ext_id: "nexus-soc", correlation_id: null, read_at: null },
    { id: "n2", ts: "2026-09-24T10:00:00Z", severity: "critical", title: "Speicher fast voll", body: "local-lvm 95 %", source_ext_id: "proxmox", correlation_id: null, read_at: null },
    { id: "n3", ts: "2026-09-23T10:00:00Z", severity: "info", title: "Alt und gelesen", body: "", source_ext_id: null, correlation_id: null, read_at: "2026-09-23T11:00:00Z" },
  ];
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "owner1", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
});

describe("NotificationsPage", () => {
  it("zeigt ohne Schreibrecht keine Knöpfe zum Als-gelesen-Markieren", async () => {
    useAuthStore.setState({
      accessToken: "tok", status: "authenticated", mfaToken: null,
      user: { id: "u2", username: "gast", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["notifications.read"] },
    });
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByTestId("notification-n1");
    expect(screen.queryByRole("button", { name: "Gelesen" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Alle als gelesen/ })).toBeNull();
  });

  it("zeigt Meldungen mit deutscher Stufe und Quelle, lange Texte eingeklappt", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const first = await screen.findByTestId("notification-n1");
    expect(within(first).getByText("Warnung")).toBeInTheDocument();
    expect(within(screen.getByTestId("notification-n2")).getByText("Kritisch")).toBeInTheDocument();
    // Die Quelle steht mit dem Namen der Erweiterung da, nicht mit ihrer Kennung.
    await waitFor(() => expect(first.textContent).toContain("Nodvard Shield"));
    expect(first.textContent).not.toContain("nexus-soc");
    expect(within(screen.getByTestId("notification-n2")).getByText(/Proxmox VE/)).toBeInTheDocument();
    expect(first.querySelector("p")?.className).toContain("line-clamp-4");
    fireEvent.click(within(first).getByRole("button", { name: "Mehr anzeigen" }));
    expect(first.querySelector("p")?.className).not.toContain("line-clamp-4");
  });

  it("Meldungen mit Zielseite haben einen Öffnen-Link", async () => {
    rows[1].payload = { path: "/ext/nexus-soc/soc?tab=guard" };
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const link = within(await screen.findByTestId("notification-n2")).getByRole("link", { name: "Öffnen →" });
    expect(link.getAttribute("href")).toBe("/ext/nexus-soc/soc?tab=guard");
    expect(within(screen.getByTestId("notification-n1")).queryByRole("link")).toBeNull();
  });

  it("einzeln und alle als gelesen markieren", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    renderPage();
    const first = await screen.findByTestId("notification-n1");

    fireEvent.click(within(first).getByRole("button", { name: "Gelesen" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/notifications/read"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/notifications/read"))?.body).toEqual({ ids: ["n1"] });
    await waitFor(() => expect(within(screen.getByTestId("notification-n1")).queryByRole("button", { name: "Gelesen" })).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Alle als gelesen markieren" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Alle als gelesen markieren" })).toBeNull());
    expect(calls.some((c) => c.url.endsWith("/notifications/read-all"))).toBe(true);
  });

  it("filtert auf ungelesene", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByTestId("notification-n3");
    fireEvent.click(screen.getByLabelText("Nur ungelesene"));
    await waitFor(() => expect(screen.queryByTestId("notification-n3")).toBeNull());
    expect(await screen.findByTestId("notification-n1")).toBeInTheDocument();
  });

  it("leerer Zustand erklaert, wofuer die Seite da ist", async () => {
    rows = [];
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    expect(await screen.findByText(/Noch keine Meldungen/)).toBeInTheDocument();
  });
});
