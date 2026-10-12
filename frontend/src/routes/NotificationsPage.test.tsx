import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { NotificationsPage, deleteReadQuestion, useUnreadNotifications } from "./NotificationsPage";

vi.mock("../lib/ws", () => ({ useWsSubscription: () => undefined }));
const confirmDialog = vi.hoisted(() => vi.fn(async (..._args: unknown[]) => true));
vi.mock("../state/dialogs", () => ({ confirmDialog, promptDialog: vi.fn(async () => null) }));

const LONG_BODY = Array.from({ length: 8 }, (_, i) => `Zeile ${i + 1} des Lageberichts`).join("\n");

/** Antwort auf `GET /extensions`. */
let extensions: { id: string; name: string; state: string; legacy_ids?: string[] }[];

/** Wenn gesetzt, antwortet DELETE /notifications/{id} mit diesem Fehler. */
let failDelete: { status: number; detail: string } | null;

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
    if (url.endsWith("/api/v1/notifications/unread-count") && method === "GET") {
      return new Response(JSON.stringify({ unread: rows.filter((r) => !r.read_at).length }));
    }
    if (url.endsWith("/api/v1/notifications/delete-read") && method === "POST") {
      const gone = rows.filter((r) => r.read_at).length;
      rows = rows.filter((r) => !r.read_at);
      return new Response(JSON.stringify({ deleted: gone }));
    }
    if (url.includes("/api/v1/notifications/") && method === "DELETE") {
      const id = decodeURIComponent(url.split("/api/v1/notifications/")[1]);
      if (failDelete) return new Response(JSON.stringify({ detail: failDelete.detail }), { status: failDelete.status });
      if (!rows.some((r) => r.id === id)) return new Response(JSON.stringify({ detail: "Unbekannte Benachrichtigung." }), { status: 404 });
      rows = rows.filter((r) => r.id !== id);
      return new Response(null, { status: 204 });
    }
    if (url.endsWith("/api/v1/extensions") && method === "GET") {
      return new Response(JSON.stringify(extensions));
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

/** Der Zähler der Glocke (Kopfzeile), im selben Abfrage-Speicher wie die Seite. */
function UnreadProbe() {
  const unread = useUnreadNotifications(true);
  return <p data-testid="unread-probe">{unread}</p>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <UnreadProbe />
        <NotificationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  failDelete = null;
  confirmDialog.mockClear();
  confirmDialog.mockImplementation(async () => true);
  extensions = [
    { id: "shield", name: "Nodvard Shield", state: "enabled" },
    { id: "proxmox", name: "Proxmox VE", state: "enabled" },
  ];
  rows = [
    { id: "n1", ts: "2026-09-24T11:00:00Z", severity: "warning", title: "Lagebericht der Container-Wache (2 Ereignisse)", body: LONG_BODY, source_ext_id: "shield", correlation_id: null, read_at: null },
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
    // Löschen betrifft alle Benutzer und braucht dasselbe Recht wie das Markieren.
    expect(screen.queryByRole("button", { name: /löschen/ })).toBeNull();
  });

  it("zeigt Meldungen mit deutscher Stufe und Quelle, lange Texte eingeklappt", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const first = await screen.findByTestId("notification-n1");
    expect(within(first).getByText("Warnung")).toBeInTheDocument();
    expect(within(screen.getByTestId("notification-n2")).getByText("Kritisch")).toBeInTheDocument();
    // Die Quelle steht mit dem Namen der Erweiterung da, nicht mit ihrer Kennung.
    await waitFor(() => expect(first.textContent).toContain("Nodvard Shield"));
    expect(first.textContent).not.toContain("shield");
    expect(within(screen.getByTestId("notification-n2")).getByText(/Proxmox VE/)).toBeInTheDocument();
    expect(first.querySelector("p")?.className).toContain("line-clamp-4");
    fireEvent.click(within(first).getByRole("button", { name: "Mehr anzeigen" }));
    expect(first.querySelector("p")?.className).not.toContain("line-clamp-4");
  });

  it("Meldungen mit der alten Kennung einer umbenannten Erweiterung zeigen trotzdem deren Namen", async () => {
    rows[0].source_ext_id = "nexus-soc";
    extensions = [
      { id: "shield", name: "Nodvard Shield", state: "enabled", legacy_ids: ["nexus-soc"] },
      { id: "proxmox", name: "Proxmox VE", state: "enabled" },
    ];
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const first = await screen.findByTestId("notification-n1");
    await waitFor(() => expect(first.textContent).toContain("Nodvard Shield"));
    expect(first.textContent).not.toContain("nexus-soc");
  });

  it("ohne `legacy_ids` bleibt es bei der Kennung, wenn es zu ihr keine Erweiterung gibt", async () => {
    rows[0].source_ext_id = "nexus-soc";
    extensions = [{ id: "shield", name: "Nodvard Shield", state: "enabled" }, { id: "proxmox", name: "Proxmox VE", state: "enabled" }];
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const first = await screen.findByTestId("notification-n1");
    await waitFor(() => expect(within(screen.getByTestId("notification-n2")).getByText(/Proxmox VE/)).toBeInTheDocument());
    expect(first.textContent).toContain("nexus-soc");
    expect(first.textContent).not.toContain("Nodvard Shield");
  });

  it("Meldungen mit Zielseite haben einen Öffnen-Link", async () => {
    rows[1].payload = { path: "/ext/shield/soc?tab=guard" };
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const link = within(await screen.findByTestId("notification-n2")).getByRole("link", { name: "Öffnen →" });
    expect(link.getAttribute("href")).toBe("/ext/shield/soc?tab=guard");
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

  it("Ladefehler: der Grund und „Erneut versuchen“ statt „Noch keine Meldungen“", async () => {
    rows = [];
    let fail = true;
    const ok = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (fail && String(input).includes("/api/v1/notifications?")) return new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status: 500 });
      return ok(input, init);
    }));
    renderPage();
    const error = await screen.findByTestId("load-error");
    expect(error.textContent).toContain("Die Liste der Meldungen ließ sich gerade nicht laden: Datenbank gesperrt");
    expect(screen.queryByText(/Noch keine Meldungen/)).toBeNull();
    fail = false;
    fireEvent.click(within(error).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText(/Noch keine Meldungen/)).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });

  it("löscht eine Meldung einzeln ohne Rückfrage, die Liste und der Zähler stimmen danach", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    renderPage();
    const first = await screen.findByTestId("notification-n1");
    await waitFor(() => expect(screen.getByTestId("unread-probe")).toHaveTextContent("2"));

    // Papierkorb mit sprechendem Namen für Screenreader.
    const trash = within(first).getByRole("button", { name: "Meldung „Lagebericht der Container-Wache (2 Ereignisse)“ löschen" });
    expect(trash).toHaveAttribute("title", "Löschen");
    fireEvent.click(trash);

    await waitFor(() => expect(screen.queryByTestId("notification-n1")).toBeNull());
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual(["/api/v1/notifications/n1"]);
    expect(confirmDialog).not.toHaveBeenCalled();
    expect(screen.getByTestId("notification-n2")).toBeInTheDocument();
    expect(screen.getByTestId("notification-n3")).toBeInTheDocument();
    // Die ungelesene wurde gelöscht: der Zähler der Glocke zieht nach.
    await waitFor(() => expect(screen.getByTestId("unread-probe")).toHaveTextContent("1"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("löscht auch eine gelesene Meldung einzeln, ohne den Zähler zu ändern", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByTestId("notification-n3");
    await waitFor(() => expect(screen.getByTestId("unread-probe")).toHaveTextContent("2"));

    fireEvent.click(within(screen.getByTestId("notification-n3")).getByRole("button", { name: /Alt und gelesen.*löschen/ }));

    await waitFor(() => expect(screen.queryByTestId("notification-n3")).toBeNull());
    expect(screen.getByTestId("unread-probe")).toHaveTextContent("2");
  });

  it("fragt vor „Alle gelesenen löschen“ nach und löscht nur die gelesenen", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    renderPage();
    await screen.findByTestId("notification-n3");

    fireEvent.click(screen.getByRole("button", { name: "Alle gelesenen löschen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    const [message, options] = confirmDialog.mock.calls[0] as [string, { danger?: boolean; confirmLabel?: string; title?: string }];
    expect(message).toBe("1 gelesene Meldung löschen? Das lässt sich nicht rückgängig machen.");
    expect(options).toMatchObject({ danger: true, confirmLabel: "Löschen" });
    await waitFor(() => expect(screen.queryByTestId("notification-n3")).toBeNull());
    expect(calls.filter((c) => c.url.endsWith("/notifications/delete-read")).map((c) => c.method)).toEqual(["POST"]);
    // Ungelesene bleiben, und weil nichts Gelesenes mehr da ist, verschwindet der Knopf.
    expect(screen.getByTestId("notification-n1")).toBeInTheDocument();
    expect(screen.getByTestId("notification-n2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Alle gelesenen löschen" })).toBeNull();
    expect(screen.getByTestId("unread-probe")).toHaveTextContent("2");
  });

  it("zählt in der Rückfrage alle gelesenen Meldungen der Liste", async () => {
    rows = rows.map((r) => ({ ...r, read_at: "2026-09-24T12:00:00Z" }));
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByTestId("notification-n1");

    fireEvent.click(screen.getByRole("button", { name: "Alle gelesenen löschen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    expect(confirmDialog.mock.calls[0][0]).toBe("3 gelesene Meldungen löschen? Das lässt sich nicht rückgängig machen.");
  });

  it("sagt bei einer vollen Liste (200 Meldungen), dass es noch mehr gelesene geben kann", async () => {
    rows = Array.from({ length: 200 }, (_, i) => ({
      id: `m${i}`, ts: "2026-09-24T10:00:00Z", severity: "info", title: `Meldung ${i}`, body: "", source_ext_id: null,
      correlation_id: null, read_at: "2026-09-24T11:00:00Z",
    }));
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByTestId("notification-m0");

    fireEvent.click(screen.getByRole("button", { name: "Alle gelesenen löschen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    expect(confirmDialog.mock.calls[0][0]).toBe(deleteReadQuestion(200, false));
  });

  it("löscht nichts, wenn die Rückfrage abgelehnt wird", async () => {
    confirmDialog.mockImplementation(async () => false);
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    renderPage();
    await screen.findByTestId("notification-n3");

    fireEvent.click(screen.getByRole("button", { name: "Alle gelesenen löschen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith("/delete-read"))).toBe(false);
    expect(screen.getByTestId("notification-n3")).toBeInTheDocument();
  });

  it("zeigt „Alle gelesenen löschen“ nur, wenn es gelesene Meldungen gibt", async () => {
    rows = rows.filter((r) => !r.read_at);
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByTestId("notification-n1");
    expect(screen.queryByRole("button", { name: "Alle gelesenen löschen" })).toBeNull();
  });

  it("zeigt einen Fehler beim Löschen und lässt die Meldung stehen", async () => {
    failDelete = { status: 500, detail: "Die Datenbank ist gerade gesperrt." };
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const first = await screen.findByTestId("notification-n1");

    fireEvent.click(within(first).getByRole("button", { name: /löschen/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Die Datenbank ist gerade gesperrt.");
    expect(screen.getByTestId("notification-n1")).toBeInTheDocument();
  });

  it("eine Meldung, die jemand anderes schon gelöscht hat (404), ist kein Fehler und verschwindet aus der Liste", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const first = await screen.findByTestId("notification-n1");
    // Zwischen Anzeigen und Klick löscht ein anderer Benutzer sie.
    rows = rows.filter((r) => r.id !== "n1");

    fireEvent.click(within(first).getByRole("button", { name: /löschen/ }));

    await waitFor(() => expect(screen.queryByTestId("notification-n1")).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("deleteReadQuestion", () => {
  it("nennt die Zahl und sagt, dass es sich nicht rückgängig machen lässt", () => {
    expect(deleteReadQuestion(1, true)).toBe("1 gelesene Meldung löschen? Das lässt sich nicht rückgängig machen.");
    expect(deleteReadQuestion(12, true)).toBe("12 gelesene Meldungen löschen? Das lässt sich nicht rückgängig machen.");
  });

  it("nennt keine Zahl, wenn die Liste am Limit ist und noch ältere gelesene dahinter stehen können", () => {
    const question = deleteReadQuestion(200, false);
    expect(question).toContain("Alle gelesenen Meldungen löschen");
    expect(question).toContain("nicht mehr in der Liste");
    expect(question).toContain("Das lässt sich nicht rückgängig machen.");
    expect(question).not.toMatch(/\d/);
  });
});
