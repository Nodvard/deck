import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { navigateTo } from "../../../_shared/frontend/src/testShell";

import { ago, GameServerPage } from "./GameServerPage";

/** Form wie `GET /ext/gameserver/servers/{id}` (Backend-Test mit echter SSH-Kette), anonymisiert. */
const SUMMARY = {
  host_id: "h1", name: "game-win", status: "up", service_label: "läuft", tone: "good", running: true,
  players_online: 2, version: "1.0.12", error: null, can_start: false, can_stop: true,
  join_code: "413749", join_code_display: "Join-Code: 413749",
};
const DETAILS = {
  ...SUMMARY,
  details: {
    service_name: "ValheimServer",
    process: { ram_mb: 1392, cpu_s: 1082, responding: true },
    server: { name: "Valheim", world: "TestWelt", port: 2456, crossplay: true, public: true, preset: null, modifiers: ["portals: casual"], has_password: true },
    join_code_age_s: 5 * 3600,
    recent_players: [{ name: "Spielerin", last_seen_age_s: 180 }, { name: "Spieler_A", last_seen_age_s: null }],
    last_save_age_s: 296,
    world: { name: "TestWelt", size: 1876559, age_s: 296 },
    auto_backups: [{ name: "TestWelt_backup_auto-20260924-093504", size: 1876559, age_s: 1800 }],
    backups: [],
    log_tail: ["09/24/2026 10:05:04: World save (5/5) done. Total time [35ms]"],
    paths: { log: "C:\\valheim\\logs\\service-out.log", world: "C:\\worlds_local", backup: "C:\\valheim\\backups" },
    error: null,
  },
  config: { profile: "valheim-windows", values: {} },
};
const PROFILES = [{
  id: "valheim-windows", label: "Valheim (Windows-Dienst)",
  fields: [
    { key: "service_name", label: "Dienstname", help: "Leer = automatisch", default: "" },
    { key: "log_path", label: "Log-Datei", help: "Leer = aus NSSM", default: "" },
  ],
}];

let calls: { url: string; method: string; body?: unknown }[];
let details: typeof DETAILS;

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.endsWith("/ext/gameserver/servers") && method === "GET") return new Response(JSON.stringify([details]), { status: 200 });
    if (url.includes("/ext/gameserver/servers/h1") && method === "GET") return new Response(JSON.stringify(details), { status: 200 });
    if (url.endsWith("/ext/gameserver/profiles")) return new Response(JSON.stringify(PROFILES), { status: 200 });
    if (url.endsWith("/config") && method === "PUT") {
      return new Response(JSON.stringify({ profile: "valheim-windows", values: { service_name: "ValheimServer" } }), { status: 200 });
    }
    if (method === "POST" && /\/servers\/h1\/(start|stop|restart|backup)$/.test(url)) {
      const risk = { start: "low", stop: "high", restart: "medium", backup: "low" }[url.split("/").pop()!];
      return new Response(JSON.stringify({ action_id: "a1", status: "proposed", risk }), { status: 200 });
    }
    if (url.endsWith("/actions/a1/approve")) return new Response(JSON.stringify({ id: "a1", status: "succeeded" }), { status: 200 });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
let hasPermission: Mock<(permission: string) => boolean>;

beforeEach(() => {
  calls = [];
  details = structuredClone(DETAILS);
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  hasPermission = vi.fn<(permission: string) => boolean>().mockReturnValue(true);
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog,
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission,
  };
  vi.stubGlobal("fetch", mockFetch());
});

describe("GameServerPage", () => {
  it("zeigt Join-Code, Spieler, Welt, Server-Daten und Sicherungen", async () => {
    render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    expect(card.textContent).toContain("413749");
    expect(card.textContent).toContain("vergeben vor 5 h");
    expect(card.textContent).toContain("Valheim (Windows-Dienst) · Version 1.0.12");
    expect(card.textContent).toContain("TestWelt · 1.9 MB");
    expect(card.textContent).toContain("Spielerin");
    expect(card.textContent).toContain("vor 3 min");
    expect(card.textContent).toContain("portals: casual");
    expect(card.textContent).toContain("gesetzt"); // Passwort: nur ob, nie welches
    expect(card.textContent).toContain("Noch keine -- „Welt sichern“ legt eine an.");
    expect(within(card).getByText("läuft").className).toContain("emerald");
  });

  it("Knöpfe passen zum Zustand: laufend -> Neustart/Sichern/Stoppen, gestoppt -> Starten", async () => {
    const { unmount } = render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    const names = () => within(screen.getByTestId("server-h1")).getAllByRole("button").map((b) => b.textContent);
    expect(names()).toEqual(expect.arrayContaining(["Neustarten", "Welt sichern", "Stoppen", "Einstellungen"]));
    expect(within(card).queryByRole("button", { name: "Starten" })).toBeNull();
    unmount();

    details = { ...structuredClone(DETAILS), running: false, can_start: true, can_stop: false, service_label: "gestoppt", tone: "danger" };
    render(<GameServerPage />);
    await screen.findByTestId("server-h1");
    expect(names()).toEqual(expect.arrayContaining(["Starten", "Einstellungen"]));
    expect(names()).not.toContain("Neustarten");
  });

  it("Neustart fragt nach und wird automatisch freigegeben, wenn berechtigt", async () => {
    render(<GameServerPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Neustarten" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true));
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Join-Code ändert sich"), expect.anything());
    expect(hasPermission).toHaveBeenCalledWith("actions.approve:medium");
    expect(await screen.findByText(/Neustarten -> erledigt/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/servers/h1/details?fresh=true"))).toBe(true);
  });

  it("Betrachter laden die Details ohne Server-Log und ohne fresh", async () => {
    hasPermission.mockReturnValue(false);
    render(<GameServerPage />);
    await screen.findByTestId("server-h1");
    const detailCalls = calls.filter((c) => c.url.includes("/servers/h1"));
    expect(detailCalls.map((c) => c.url)).toEqual(["/api/v1/ext/gameserver/servers/h1"]);
  });

  it("mit Ausführen-Recht kommen Details samt Log von der Detail-Route", async () => {
    render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    expect(calls.some((c) => c.url.endsWith("/servers/h1/details"))).toBe(true);
    expect(card.textContent).toContain("Server-Log (letzte 1 Zeilen)");
  });

  it("Welt sichern ohne Rückfrage; ohne Freigabe-Recht nur Vorschlag mit Hinweis", async () => {
    hasPermission.mockImplementation((p: string) => !p.startsWith("actions.approve"));
    render(<GameServerPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Welt sichern" }));
    expect(await screen.findByText(/vorgeschlagen -- Freigabe durch einen Admin nötig/)).toBeInTheDocument();
    expect(confirmDialog).not.toHaveBeenCalled();
    expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
  });

  it("Einstellungen: erkannte Werte als Platzhalter, Speichern schickt Profil + Werte", async () => {
    render(<GameServerPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Einstellungen" }));
    const panel = await screen.findByTestId("settings-h1");
    const service = within(panel).getByLabelText("Dienstname") as HTMLInputElement;
    expect(service.placeholder).toBe("automatisch: ValheimServer");
    fireEvent.change(service, { target: { value: "ValheimServer" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ profile: "valheim-windows", values: { service_name: "ValheimServer" } }));
    await waitFor(() => expect(screen.queryByTestId("settings-h1")).toBeNull());
  });

  it("ohne Rechte keine Aktions- und Einstellungs-Knöpfe", async () => {
    hasPermission.mockReturnValue(false);
    render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    expect(within(card).queryAllByRole("button").map((b) => b.textContent)).toEqual(["Kopieren"]);
  });

  it("kopiert den Join-Code in die Zwischenablage", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<GameServerPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Kopieren" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("413749"));
    expect(await screen.findByRole("button", { name: "Kopiert!" })).toBeInTheDocument();
  });

  it("Altersangaben", () => {
    expect([ago(null), ago(30), ago(600), ago(7200), ago(3 * 86400)]).toEqual(["unbekannt", "gerade eben", "vor 10 min", "vor 2 h", "vor 3 T"]);
  });

  it("Sprung von der Server-Seite (?host=): scrollt zu genau diesem Server", async () => {
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this); };
    window.history.replaceState({}, "", "/ext/gameserver/gameservers?host=h1");
    try {
      vi.stubGlobal("fetch", mockFetch());
      render(<GameServerPage />);
      await screen.findByTestId("server-h1");
      await waitFor(() => expect(scrolled.map((e) => e.id)).toEqual(["host-h1"]));
    } finally {
      Element.prototype.scrollIntoView = original;
      window.history.replaceState({}, "", "/");
    }
  });

  it("derselbe Link noch einmal (Seite schon offen, weggescrollt) scrollt wieder hin", async () => {
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this); };
    window.history.replaceState({}, "", "/ext/gameserver/gameservers?host=h1");
    try {
      render(<GameServerPage />);
      await waitFor(() => expect(scrolled.map((e) => e.id)).toEqual(["host-h1"]));

      // Für den Router ist das dieselbe Adresse -- die Seite muss trotzdem wieder hinscrollen.
      navigateTo("/ext/gameserver/gameservers?host=h1");
      await waitFor(() => expect(scrolled.map((e) => e.id)).toEqual(["host-h1", "host-h1"]));

      // Ein Link ohne ?host= (Menü) scrollt nirgendwohin.
      navigateTo("/ext/gameserver/gameservers");
      await new Promise((r) => setTimeout(r, 20));
      expect(scrolled).toHaveLength(2);
    } finally {
      Element.prototype.scrollIntoView = original;
      window.history.replaceState({}, "", "/");
    }
  });
});

describe("GameServerPage ohne Gameserver", () => {
  function emptyFetch(tag?: string) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/ext/gameserver/servers")) return new Response("[]", { status: 200 });
      if (url.endsWith("/ext/gameserver/profiles")) return new Response("[]", { status: 200 });
      if (url.endsWith("/extensions/gameserver/settings")) return new Response(JSON.stringify({ values: tag ? { host_tag: tag } : {} }), { status: 200 });
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
  }

  it("sagt in normalem Deutsch, was zu tun ist, mit Links zu Server & Zugänge und zum Modul", async () => {
    emptyFetch();
    render(<GameServerPage />);
    expect(await screen.findByText("Noch kein Gameserver eingerichtet")).toBeInTheDocument();
    expect(screen.getByText(/Markierung „gameserver“/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Server & Zugänge öffnen" })).toHaveAttribute("href", "/settings/hosts");
    expect(screen.getByRole("link", { name: "Moduleinstellungen" })).toHaveAttribute("href", "/settings/extensions/gameserver");
    expect(document.body.textContent).not.toContain("getaggt");
  });

  it("nennt die in den Einstellungen gewählte Markierung", async () => {
    emptyFetch("spiele");
    render(<GameServerPage />);
    expect(await screen.findByText(/Markierung „spiele“/)).toBeInTheDocument();
  });

  it("zeigt Links nur mit dem Recht dafür", async () => {
    hasPermission.mockImplementation((p) => p === "hosts.write");
    emptyFetch();
    render(<GameServerPage />);
    await screen.findByText("Noch kein Gameserver eingerichtet");
    expect(screen.getByRole("link", { name: "Server & Zugänge öffnen" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Moduleinstellungen" })).toBeNull();
  });
});

