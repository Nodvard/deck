import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationOut } from "../routes/NotificationsPage";
import { NotificationBell, notificationHeadline } from "./NotificationBell";

const now = Date.now();
const ago = (min: number) => new Date(now - min * 60_000).toISOString();

function row(over: Partial<NotificationOut> & { id: string }): NotificationOut {
  return { ts: ago(5), severity: "info", title: "Titel", body: "", source_ext_id: null, correlation_id: null, read_at: null, payload: {}, ...over };
}

let rows: NotificationOut[];
let calls: { url: string; method: string; body?: unknown }[];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes("/api/v1/notifications?") && method === "GET") return new Response(JSON.stringify(rows));
    if (url.endsWith("/notifications/read") && method === "POST") return new Response(JSON.stringify({ marked: 1 }));
    if (url.endsWith("/notifications/read-all") && method === "POST") return new Response(JSON.stringify({ marked: 2 }));
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

function renderBell(unread = 2) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/start"]}>
        <div>
          <NotificationBell unread={unread} />
          <button type="button">draussen</button>
        </div>
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const bell = () => screen.getByRole("button", { name: /^Meldungen/ });

beforeEach(() => {
  calls = [];
  rows = [
    row({ id: "a", severity: "critical", title: "Speicher fast voll", source_ext_id: "proxmox", payload: { path: "/hosts/pve2" } }),
    row({ id: "b", severity: "warning", title: "Lagebericht", source_ext_id: "nexus-soc", ts: ago(90) }),
    row({ id: "c", title: "Alt und gelesen", read_at: ago(10), ts: ago(60 * 30) }),
  ];
  vi.stubGlobal("fetch", mockFetch());
});

describe("NotificationBell", () => {
  it("ist zu, bis man auf die Glocke drückt, und meldet das per aria", () => {
    renderBell();
    expect(bell().getAttribute("aria-haspopup")).toBe("dialog");
    expect(bell().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("öffnet per Klick, holt den Fokus ins Fenster und schließt wieder per Klick", async () => {
    renderBell();
    fireEvent.click(bell());
    const dialog = await screen.findByRole("dialog", { name: "Meldungen" });
    expect(bell().getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement).toBe(dialog);
    expect(calls.find((c) => c.url.includes("/notifications?limit=8"))).toBeTruthy();
    fireEvent.click(bell());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("schließt mit Esc und gibt den Fokus an die Glocke zurück", async () => {
    renderBell();
    fireEvent.click(bell());
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(bell());
  });

  it("schließt bei Klick außerhalb, nicht bei Klick im Fenster", async () => {
    renderBell();
    fireEvent.click(bell());
    const dialog = await screen.findByRole("dialog");
    fireEvent.pointerDown(within(dialog).getByText("Meldungen"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("button", { name: "draussen" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("zeigt Einträge mit Quelle und relativer Zeit und hebt Ungelesene hervor", async () => {
    renderBell();
    fireEvent.click(bell());
    const a = await screen.findByTestId("bell-item-a");
    expect(a.dataset.unread).toBe("true");
    expect(a.textContent).toContain("Speicher fast voll");
    expect(a.textContent).toContain("vor 5 min");
    expect(a.textContent).toContain("proxmox");
    expect(a.textContent).toContain("Kritisch");
    expect(screen.getByTestId("bell-item-b").textContent).toContain("vor 1 h");
    expect(screen.getByTestId("bell-item-c").dataset.unread).toBe("false");
    expect(screen.getByTestId("bell-unread").textContent).toBe("2 ungelesen");
  });

  it("Einträge sind per Tab erreichbar (echte Knöpfe)", async () => {
    renderBell();
    fireEvent.click(bell());
    const a = await screen.findByTestId("bell-item-a");
    expect(within(a).getByRole("button")).toBeInTheDocument();
    expect(screen.getAllByRole("button").filter((b) => b.tabIndex < 0)).toHaveLength(0);
  });

  it("Klick auf einen Eintrag markiert ihn als gelesen und springt zu payload.path", async () => {
    renderBell();
    fireEvent.click(bell());
    fireEvent.click(within(await screen.findByTestId("bell-item-a")).getByRole("button"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/notifications/read"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/notifications/read"))?.body).toEqual({ ids: ["a"] });
    expect(screen.getByTestId("where").textContent).toBe("/hosts/pve2");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("ohne payload.path geht es auf die Meldungsseite; Gelesene werden nicht erneut markiert", async () => {
    renderBell();
    fireEvent.click(bell());
    fireEvent.click(within(await screen.findByTestId("bell-item-c")).getByRole("button"));
    expect(screen.getByTestId("where").textContent).toBe("/notifications");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("nimmt nur Ziele innerhalb des Dashboards", async () => {
    rows = [row({ id: "x", payload: { path: "//evil.example/x" } })];
    renderBell(1);
    fireEvent.click(bell());
    fireEvent.click(within(await screen.findByTestId("bell-item-x")).getByRole("button"));
    expect(screen.getByTestId("where").textContent).toBe("/notifications");
  });

  it("„Alle als gelesen“ ruft read-all auf und fehlt, wenn nichts ungelesen ist", async () => {
    const { unmount } = renderBell(2);
    fireEvent.click(bell());
    fireEvent.click(await screen.findByRole("button", { name: "Alle als gelesen" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/notifications/read-all"))).toBe(true));
    unmount();
    renderBell(0);
    fireEvent.click(bell());
    await screen.findByRole("dialog");
    expect(screen.queryByRole("button", { name: "Alle als gelesen" })).toBeNull();
    expect(screen.getByTestId("bell-unread").textContent).toBe("alles gelesen");
  });

  it("leerer Zustand ist freundlich", async () => {
    rows = [];
    renderBell(0);
    fireEvent.click(bell());
    expect((await screen.findByTestId("bell-empty")).textContent).toContain("Alles ruhig");
    expect(screen.getByTestId("bell-empty").textContent).toContain("Keine neuen Meldungen");
  });

  it("„Alle Meldungen anzeigen“ führt zur Seite und schließt das Fenster", async () => {
    renderBell();
    fireEvent.click(bell());
    const link = await screen.findByRole("link", { name: /Alle Meldungen anzeigen/ });
    expect(link.getAttribute("href")).toBe("/notifications");
    fireEvent.click(link);
    expect(screen.getByTestId("where").textContent).toBe("/notifications");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lädt bei jedem Öffnen frisch", async () => {
    renderBell();
    fireEvent.click(bell());
    await screen.findByTestId("bell-item-a");
    fireEvent.click(bell());
    rows = [row({ id: "neu", title: "Ganz neu" })];
    fireEvent.click(bell());
    expect(await screen.findByTestId("bell-item-neu")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("/notifications?limit=8"))).toHaveLength(2);
  });

  it("schließt, wenn der Fokus aus dem Fenster wandert", async () => {
    renderBell();
    fireEvent.click(bell());
    await screen.findByRole("dialog");
    act(() => screen.getByRole("button", { name: "draussen" }).focus());
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("notificationHeadline", () => {
  it("nimmt den Titel, sonst die erste nicht leere Zeile des Textes", () => {
    expect(notificationHeadline({ title: " Hallo ", body: "x" })).toBe("Hallo");
    expect(notificationHeadline({ title: "", body: "\n Erste\nZweite" })).toBe("Erste");
    expect(notificationHeadline({ title: "", body: "" })).toBe("Meldung");
  });
});
