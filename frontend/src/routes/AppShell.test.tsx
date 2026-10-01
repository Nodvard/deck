import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SEEN_KEY } from "../lib/changelog";
import { getDeckTimezone, setDeckTimezone } from "../lib/deckTimezone";
import { previewState, respond } from "../preview/fixtures";
import { useWsSubscription } from "../lib/ws";
import { useAuthStore } from "../state/auth";
import type { PageOut } from "../widgets/types";
import { AppShell, groupPagesBySection } from "./AppShell";
import { AboutSettings } from "./settings/AboutSettings";

vi.mock("../lib/ws", () => ({
  wsClient: { ensureConnected: vi.fn(), disconnect: vi.fn() },
  useWsSubscription: vi.fn(),
}));

const page = (id: string, title: string, nav_section: string | null, nav_order: number, show_in_nav = true): PageOut => ({
  ext_id: id, id, path: `/${id}`, title, icon: null, nav_section, nav_order, permissions: [], component: "X", mobile: "widgets", show_in_nav,
});

describe("groupPagesBySection", () => {
  it("Abschnitte mit einer einzigen Seite landen gesammelt unter 'Werkzeuge'", () => {
    const sections = groupPagesBySection([
      page("backups", "Backups", "Infrastruktur", 20),
      page("nodes", "Proxmox", "Infrastruktur", 10),
      page("documents", "Dokumente", "Dokumente", 10),
      page("inventory", "Inventar", "Inventar", 11),
      page("hidden", "Versteckt", "Infrastruktur", 5, false),
    ]);
    expect([...sections.keys()]).toEqual(["Infrastruktur", "Werkzeuge"]);
    expect(sections.get("Infrastruktur")!.map((p) => p.title)).toEqual(["Proxmox", "Backups"]);
    expect(sections.get("Werkzeuge")!.map((p) => p.title)).toEqual(["Dokumente", "Inventar"]);
  });

  it("eine einzelne Einzelseite behält ihren eigenen Abschnitt", () => {
    const sections = groupPagesBySection([page("soc", "Nodvard Shield", "Sicherheit", 20)]);
    expect([...sections.keys()]).toEqual(["Sicherheit"]);
  });
});

describe("AppShell: Token vorab erneuern", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function renderShell(visibility: DocumentVisibilityState) {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    const refreshes: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (path === "/api/v1/auth/refresh") {
        refreshes.push(path);
        return new Response(JSON.stringify({ access_token: "neu", user: useAuthStore.getState().user }), { status: 200 });
      }
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
    }));
    useAuthStore.setState({
      accessToken: "alt",
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
      status: "authenticated",
      mfaToken: null,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AppShell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return refreshes;
  }

  it("zurück in den sichtbaren Tab nach Ablauf: holt ein neues Token, bevor eine Erweiterung 401 bekommt", async () => {
    const refreshes = renderShell("visible");
    await screen.findByText("Nico");
    vi.setSystemTime(Date.now() + 20 * 60_000);
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(useAuthStore.getState().accessToken).toBe("neu"));
    expect(refreshes).toHaveLength(1);
  });

  it("Hintergrund-Tab erneuert nicht (das Refresh-Token wird ohne Gnadenfrist rotiert)", async () => {
    const refreshes = renderShell("hidden");
    await screen.findByText("Nico");
    vi.setSystemTime(Date.now() + 20 * 60_000);
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(refreshes).toHaveLength(0);
    expect(useAuthStore.getState().accessToken).toBe("alt");
  });
});

describe("AppShell: Version und Änderungsprotokoll", () => {
  // Stand der Vorschau-Daten (preview/fixtures.ts): Version 0.4.0, ein unveröffentlichter Eintrag.
  const CURRENT_STAMP = "0.4.0|1";

  beforeEach(() => window.localStorage.clear());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function renderShell(changelogStatus = 200) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (path === "/api/v1/app/changelog" && changelogStatus !== 200) {
        return new Response(JSON.stringify({ detail: "kaputt" }), { status: changelogStatus });
      }
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
    }));
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
      status: "authenticated",
      mfaToken: null,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<p>Startseite</p>} />
              <Route path="/settings/about" element={<AboutSettings />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("zeigt unten in der Seitenleiste die Version als Link auf „Über Nodvard Deck“", async () => {
    renderShell();
    const link = await screen.findByRole("link", { name: "v0.4.0" });
    expect(link).toHaveAttribute("href", "/settings/about");
    expect(link).toHaveAttribute("title", "Änderungsprotokoll öffnen");
    // Es ist dieselbe Seitenleiste wie im Handy-Menü -- der Link liegt also auch dort.
    expect(link.closest("aside")).toHaveAttribute("id", "hauptmenue");
  });

  it("beim ersten Besuch keine Neu-Markierung, der Stand wird still gemerkt", async () => {
    renderShell();
    await screen.findByRole("link", { name: "v0.4.0" });
    await waitFor(() => expect(window.localStorage.getItem(SEEN_KEY)).toBe(CURRENT_STAMP));
    expect(screen.queryByTestId("changelog-neu")).not.toBeInTheDocument();
  });

  it("neuer Stand: „Neu“ am Link, und die Seite „Über Nodvard Deck“ räumt es ab", async () => {
    window.localStorage.setItem(SEEN_KEY, "0.3.0|0");
    renderShell();
    const link = await screen.findByRole("link", { name: "v0.4.0 Neu" });
    expect(screen.getByTestId("changelog-neu")).toBeInTheDocument();

    fireEvent.click(link);
    await screen.findByRole("heading", { name: "Über Nodvard Deck" });
    await waitFor(() => expect(screen.queryByTestId("changelog-neu")).not.toBeInTheDocument());
    expect(window.localStorage.getItem(SEEN_KEY)).toBe(CURRENT_STAMP);
    expect(screen.getByRole("link", { name: "v0.4.0" })).toBeInTheDocument();
  });

  it("ohne Speicher: die Seitenleiste funktioniert, es gibt nur nie eine Markierung", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("gesperrt"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("gesperrt"); });
    renderShell();
    expect(await screen.findByRole("link", { name: "v0.4.0" })).toBeInTheDocument();
    expect(screen.queryByTestId("changelog-neu")).not.toBeInTheDocument();
  });

  it("Handy-Menü: klappt auch zu, wenn man den Eintrag der schon offenen Seite antippt", async () => {
    renderShell();
    const toggle = await screen.findByRole("button", { name: "Menü öffnen" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    // Dieselbe Adresse (Seiten ändern ihren Reiter per replaceState am Router vorbei):
    // aus Router-Sicht ändern sich Pfad und Abfrage nicht, nur der Schlüssel der Navigation.
    fireEvent.click(screen.getByRole("link", { name: "Übersicht" }));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "false"));
  });

  it("schlägt die Abfrage fehl, fehlt nur die Versionsnummer -- der Rest der Seitenleiste bleibt", async () => {
    renderShell(500);
    await screen.findByText("Nico");
    expect(screen.queryByRole("link", { name: /^v\d/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Übersicht" })).toBeInTheDocument();
  });
});

describe("AppShell: Zeitzone des Dashboards", () => {
  beforeEach(() => {
    window.__lattice ??= {} as Window["__lattice"];
    setDeckTimezone(null);
  });
  afterEach(() => {
    setDeckTimezone(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function renderShell(meStatus = 200) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (path === "/api/v1/me" && meStatus !== 200) return new Response(JSON.stringify({ detail: "kaputt" }), { status: meStatus });
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
    }));
    useAuthStore.setState({
      accessToken: "tok",
      // Kein `settings.write`: jeder Angemeldete bekommt die Zone aus GET /me.
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["hosts.read"] },
      status: "authenticated",
      mfaToken: null,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes><Route element={<AppShell />}><Route path="/" element={<p>Startseite</p>} /></Route></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("lädt die Zone beim Start aus GET /me für die Zeitplan-Wähler", async () => {
    renderShell();
    await waitFor(() => expect(getDeckTimezone()).toBe("Europe/Berlin"));
  });

  it("ohne Antwort bleibt die Zone leer, die Wähler rechnen dann in der Zeit des Geräts", async () => {
    renderShell(500);
    await screen.findByText("Startseite");
    expect(getDeckTimezone()).toBeNull();
  });
});

describe("AppShell: Beispieldaten-Band", () => {
  afterEach(() => {
    previewState.demo = false;
    vi.unstubAllGlobals();
  });

  function renderShell() {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
    }));
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
      status: "authenticated",
      mfaToken: null,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AppShell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("zeigt über jeder Seite das Band, solange Beispieldaten da sind, und nimmt es nach dem Löschen weg", async () => {
    previewState.demo = true;
    renderShell();
    const banner = await screen.findByTestId("demo-banner");
    expect(banner.textContent).toContain("Du siehst Beispieldaten.");
    expect(banner.compareDocumentPosition(document.querySelector("main")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("ohne Beispieldaten: kein Band", async () => {
    previewState.demo = false;
    renderShell();
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Hauptnavigation" })).toBeInTheDocument());
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("demo-banner")).toBeNull();
  });
});

describe("AppShell: Serverzustand live", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.mocked(useWsSubscription).mockClear();
  });

  it("lädt Server und Cockpit neu, wenn die Erreichbarkeitsprüfung einen Wechsel meldet", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
    }));
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
      status: "authenticated",
      mfaToken: null,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["hosts", "all"], []);
    client.setQueryData(["overview"], {});
    client.setQueryData(["pages"], []);
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AppShell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText("Nico");
    const events = vi.mocked(useWsSubscription).mock.calls.filter(([channel]) => channel === "events").at(-1)![1];

    events({ name: "notification.created" });
    expect(client.getQueryState(["hosts", "all"])?.isInvalidated).toBe(false);

    events({ name: "host.status_changed", data: { host_id: "h1", status: "down" } });
    expect(client.getQueryState(["hosts", "all"])?.isInvalidated).toBe(true);
    expect(client.getQueryState(["overview"])?.isInvalidated).toBe(true);
  });
});
