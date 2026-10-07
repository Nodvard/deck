import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { DashboardPage } from "./DashboardPage";

/**
 * Bisher gab es fuer DashboardPage/WidgetPickerDialog keinen
 * Test -- der erste hier, echte Fetch-Mocks + echtes Rendern (wie
 * SetupPage.test.tsx), kein `react-grid-layout`-Mock noetig (die Bibliothek
 * faellt in jsdom mangels `ResizeObserver` sauber auf `width=0` zurueck, statt
 * zu crashen -- Kinder werden trotzdem gemountet).
 */
const WIDGET_A = {
  id: "summary", ext_id: "backups", title: "Backup-Center", icon: null, description: null,
  size: { w: 2, h: 2, min_w: 1, min_h: 1 },
  refresh: { interval_s: null, ws_channel: null },
  data_endpoint: "widgets/summary",
  view: { kind: "stat", value: "{{ count }}", label: "Jobs", delta: null, tone: "neutral", sparkline_field: null },
  permissions: [], component: null, default_enabled: true,
};
const WIDGET_B = {
  id: "overview", ext_id: "proxmox", title: "Proxmox-Uebersicht", icon: null, description: null,
  size: { w: 2, h: 2, min_w: 1, min_h: 1 },
  refresh: { interval_s: null, ws_channel: null },
  data_endpoint: "widgets/overview",
  view: { kind: "stat", value: "{{ count }}", label: "Hosts", delta: null, tone: "neutral", sparkline_field: null },
  permissions: [], component: null, default_enabled: true,
};

let layoutItems: unknown[];
/** Jede gespeicherte Fassung des Layouts (PUT), die erste ist die der Auto-Platzierung. */
let puts: unknown[][];

/** Was tatsaechlich auf dem Dashboard steht -- entfernte Widgets bleiben als
 * `config.hidden` im gespeicherten Layout (siehe lib/dashboard.ts). */
function visible(): { widget_id: string }[] {
  return (layoutItems as { widget_id: string; config?: { hidden?: boolean } }[]).filter((i) => i.config?.hidden !== true);
}

function mockFetch(catalog: unknown[] = [WIDGET_A, WIDGET_B]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    if (url.endsWith("/api/v1/widgets") && method === "GET") {
      return new Response(JSON.stringify(catalog), { status: 200 });
    }
    if (url.endsWith("/api/v1/dashboard/layouts") && method === "GET") {
      return new Response(
        JSON.stringify([{ id: "layout-1", name: "Standard", is_default: true, items: layoutItems, created_at: "", updated_at: "" }]),
        { status: 200 },
      );
    }
    if (url.endsWith("/api/v1/dashboard/layouts/layout-1") && method === "PUT") {
      const body = JSON.parse(init!.body as string);
      layoutItems = body.items;
      puts.push(body.items);
      return new Response(
        JSON.stringify({ id: "layout-1", name: "Standard", is_default: true, items: layoutItems, created_at: "", updated_at: "" }),
        { status: 200 },
      );
    }
    if (url.endsWith("/api/v1/ext/backups/widgets/summary") && method === "GET") {
      return new Response(JSON.stringify({ data: { count: 2 }, meta: {} }), { status: 200 });
    }
    if (url.endsWith("/api/v1/ext/renamed/widgets/summary") && method === "GET") {
      return new Response(JSON.stringify({ data: { count: 5 }, meta: {} }), { status: 200 });
    }
    if (url.endsWith("/api/v1/ext/proxmox/widgets/overview") && method === "GET") {
      return new Response(JSON.stringify({ data: { count: 7 }, meta: {} }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
  // BEIDE Widgets starten platziert -- sonst wuerde `appendMissingWidgets()`
  // (die bestehende Auto-Platzierung, siehe DashboardPage.tsx) das fehlende
  // sofort selbst hinzufuegen und einen PUT ausloesen, noch bevor der Dialog
  // ueberhaupt geoeffnet wird. Diese Tests pruefen die NEUE manuelle
  // Hinzufuegen/Entfernen-Faehigkeit, nicht die bereits bestehende Auto-Platzierung.
  layoutItems = [
    { widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} },
    { widget_id: "overview", ext_id: "proxmox", x: 2, y: 0, w: 2, h: 2, config: {} },
  ];
  puts = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openPicker() {
  fireEvent.click(screen.getByRole("button", { name: "Widgets verwalten" }));
  return within(await screen.findByRole("dialog"));
}

describe("DashboardPage Rasterbreite", () => {
  /**
   * Live gefunden (Dashboard-Kacheln abgeschnitten): das Raster
   * war beim ersten Laden immer fest 1280px breit, unabhaengig von der Seitenbreite
   * -- auf einem Laptop wurden die rechten Karten abgeschnitten. `useContainerWidth`
   * (react-grid-layout) misst nur einmal beim Mount; das gemessene <div> existierte
   * da aber noch nicht (Early-Return fuer "Lade Dashboard …"), also blieb es beim
   * 1280px-Default und kein ResizeObserver wurde je angehaengt.
   */
  // Kartenbreite einer w=2-Karte bei Rasterbreite W (12 Spalten, 12px Abstand, RGL):
  // 2*(W-156)/12 + 12. Der alte, festgeklemmte 1280px-Default ergab 199px.
  async function cardWidthForContainer(containerWidth: number): Promise<number> {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => containerWidth });
    try {
      vi.stubGlobal("fetch", mockFetch());
      renderDashboard();
      const card = (await screen.findByText("Jobs")).closest(".react-grid-item") as HTMLElement;
      expect(card).not.toBeNull();
      return parseFloat(card.style.width);
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, "clientWidth", original);
    }
  }

  it("richtet das Raster beim ERSTEN Laden an der echten Containerbreite aus, nicht am 1280px-Default", async () => {
    // 1100px Container (typischer Laptop) -> ~169px; der alte Bug haette 199px ergeben.
    const width = await cardWidthForContainer(1100);
    expect(width).toBeGreaterThan(160);
    expect(width).toBeLessThan(180);
  });

  it("quetscht Karten auf schmalen Containern nicht unter die Mindestbreite", async () => {
    // 800px Container (Tablet) -> Raster bleibt bei 960px (waagerecht scrollbar), w=2-Karte
    // ~146px statt der ~121px, die ein reines Mitschrumpfen ergaebe.
    const width = await cardWidthForContainer(800);
    expect(width).toBeGreaterThan(140);
    expect(width).toBeLessThan(150);
  });

  it("stapelt die Karten am Handy in einer Spalte, ohne Raster und ohne das Layout zu speichern", async () => {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 390 });
    try {
      const fetchMock = mockFetch();
      vi.stubGlobal("fetch", fetchMock);
      renderDashboard();
      const card = await screen.findByText("Jobs");
      expect(card.closest(".react-grid-item")).toBeNull();
      expect(screen.getByTestId("widget-stack")).toBeInTheDocument();
      const puts = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      expect(puts).toHaveLength(0);
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, "clientWidth", original);
    }
  });
});

describe("DashboardPage + WidgetPickerDialog", () => {
  it("zeigt im Dialog den korrekten Haekchen-Status fuer beide platzierten Widgets", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderDashboard();

    await screen.findByText("Jobs"); // WidgetCard fuer "summary" ist gerendert
    const dialog = await openPicker();

    const backupsRow = (await dialog.findByText("Backup-Center")).closest("label")!;
    const proxmoxRow = (await dialog.findByText("Proxmox-Uebersicht")).closest("label")!;
    expect(backupsRow.querySelector("input")).toBeChecked();
    expect(proxmoxRow.querySelector("input")).toBeChecked();
  });

  it("entfernt ein Widget und fuegt es ueber denselben Dialog wieder hinzu", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderDashboard();

    await screen.findByText("Jobs");
    await screen.findByText("Hosts");

    let dialog = await openPicker();
    const backupsCheckbox = (await dialog.findByText("Backup-Center")).closest("label")!.querySelector("input")!;
    fireEvent.click(backupsCheckbox);

    await waitFor(() => expect(visible()).toHaveLength(1));
    expect(visible()[0].widget_id).toBe("overview");
    fireEvent.click(screen.getByRole("button", { name: "Fertig" }));
    await waitFor(() => expect(screen.queryByText("Jobs")).not.toBeInTheDocument());
    expect(screen.getByText("Hosts")).toBeInTheDocument(); // das andere Widget bleibt unberuehrt

    dialog = await openPicker();
    const backupsCheckboxAgain = (await dialog.findByText("Backup-Center")).closest("label")!.querySelector("input")!;
    expect(backupsCheckboxAgain).not.toBeChecked();
    fireEvent.click(backupsCheckboxAgain);

    await waitFor(() => expect(visible()).toHaveLength(2));
    expect(layoutItems).toHaveLength(2); // kein Doppeleintrag beim Wiedereinblenden
    fireEvent.click(screen.getByRole("button", { name: "Fertig" }));
    await screen.findByText("Jobs"); // die Karte ist wieder da
  });

  it("entfernt alle Widgets -- Dashboard zeigt den Leerzustand, der Picker bleibt erreichbar", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderDashboard();

    await screen.findByText("Jobs");
    await screen.findByText("Hosts");

    let dialog = await openPicker();
    fireEvent.click((await dialog.findByText("Backup-Center")).closest("label")!.querySelector("input")!);
    await waitFor(() => expect(visible()).toHaveLength(1));

    dialog = within(screen.getByRole("dialog"));
    fireEvent.click((await dialog.findByText("Proxmox-Uebersicht")).closest("label")!.querySelector("input")!);
    await waitFor(() => expect(visible()).toHaveLength(0));
    fireEvent.click(screen.getByRole("button", { name: "Fertig" }));

    await screen.findByText(/Keine Widgets auf dem Dashboard/);
    // Der Leerzustand führt zum Ziel: ein Knopf öffnet dieselbe Auswahl wie „Widgets verwalten“.
    fireEvent.click(screen.getByRole("button", { name: "Widgets auswählen" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Fertig" }));
    // Der Picker-Knopf bleibt erreichbar, auch wenn das Dashboard leer ist --
    // sonst gaebe es keinen Weg zurueck (siehe Modul-Docstring).
    expect(screen.getByRole("button", { name: "Widgets verwalten" })).toBeInTheDocument();
  });

  it("ein entferntes Widget bleibt auch nach dem Neuladen weg (Auto-Platzierung holt es nicht zurück)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const first = renderDashboard();
    await screen.findByText("Jobs");

    const dialog = await openPicker();
    fireEvent.click((await dialog.findByText("Backup-Center")).closest("label")!.querySelector("input")!);
    await waitFor(() => expect(visible()).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Fertig" }));
    first.unmount();

    // Neu laden: frischer Mount, frischer Query-Cache -- genau der Fall, in dem vorher
    // appendMissingWidgets() das Widget als "fehlend" wieder angehaengt hat.
    renderDashboard();
    await screen.findByText("Hosts");
    expect(screen.queryByText("Jobs")).not.toBeInTheDocument();
    // Auch nach allen Speichervorgaengen des zweiten Mounts (react-grid-layout meldet
    // beim Mount sein Layout) bleibt es ausgeblendet -- und steht nicht doppelt drin.
    await waitFor(() => expect(visible().map((i) => i.widget_id)).toEqual(["overview"]));
    expect(layoutItems).toHaveLength(2);
    expect(screen.queryByText("Jobs")).not.toBeInTheDocument();
  });
});

/**
 * Eine umbenannte Erweiterung (`legacy_ext_ids` im Katalog): ihr Dashboard-Eintrag unter der alten Kennung
 * zieht mit um, statt dass ihr Widget als neu unten angehaengt wird.
 */
describe("DashboardPage: Widgets einer umbenannten Erweiterung", () => {
  const RENAMED = { ...WIDGET_A, ext_id: "renamed", legacy_ext_ids: ["old"] };
  // Rechts oben (x=6, y=0): das Raster schiebt Karten nur nach oben/links zusammen, diese Stelle bleibt also stehen.
  const OLD_ENTRY = { widget_id: "summary", ext_id: "old", x: 6, y: 0, w: 5, h: 3, config: { farbe: "blau" } };
  const OTHER_ENTRY = { widget_id: "overview", ext_id: "proxmox", x: 0, y: 0, w: 2, h: 2, config: {} };

  const hasRenamed = (items: unknown[]) => items.some((i) => (i as { ext_id: string }).ext_id === "renamed");
  /** Die erste gespeicherte Fassung, in der das Widget unter der heutigen Kennung steht. Die Reihenfolge der Speichervorgänge
   * ist nicht fest: das Raster meldet beim Einhängen zusätzlich sein eigenes Layout. */
  async function savedWithRenamed(): Promise<unknown[]> {
    await waitFor(() => expect(puts.some(hasRenamed)).toBe(true));
    return puts.find(hasRenamed)!;
  }

  it("übernimmt Position, Größe und Einstellungen des alten Eintrags; der alte Eintrag bleibt in den Daten", async () => {
    layoutItems = [OLD_ENTRY, OTHER_ENTRY];
    vi.stubGlobal("fetch", mockFetch([RENAMED, WIDGET_B]));
    renderDashboard();

    expect(await screen.findByText("Jobs")).toBeInTheDocument();
    expect(await savedWithRenamed()).toEqual([OLD_ENTRY, OTHER_ENTRY, { ...OLD_ENTRY, ext_id: "renamed" }]);
    // Der Eintrag unter der alten Kennung bleibt für den Rückweg auf die alte Version erhalten, in jeder gespeicherten Fassung.
    for (const saved of puts) expect(saved).toContainEqual(OLD_ENTRY);
    // Das Widget steht genau einmal auf dem Dashboard: der alte Eintrag wird nicht gezeigt.
    expect(screen.getAllByText("Backup-Center")).toHaveLength(1);
    expect(screen.getAllByText("Jobs")).toHaveLength(1);
    // Und es holt seine Daten unter der heutigen Kennung.
    const urls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url.endsWith("/api/v1/ext/renamed/widgets/summary"))).toBe(true);
  });

  it("ein dort entferntes Widget bleibt entfernt (config.hidden wird mitgenommen) und kommt nicht neu dazu", async () => {
    layoutItems = [{ ...OLD_ENTRY, config: { hidden: true } }];
    vi.stubGlobal("fetch", mockFetch([RENAMED]));
    renderDashboard();

    await screen.findByText(/Keine Widgets auf dem Dashboard/);
    expect(await savedWithRenamed()).toEqual([
      { ...OLD_ENTRY, config: { hidden: true } },
      { ...OLD_ENTRY, ext_id: "renamed", config: { hidden: true } },
    ]);
    expect(screen.queryByText("Backup-Center")).toBeNull();
  });

  it("steht der Zwilling schon im Layout (nächstes Laden), kommt kein weiterer Eintrag dazu", async () => {
    const twin = { ...OLD_ENTRY, ext_id: "renamed" };
    layoutItems = [OLD_ENTRY, OTHER_ENTRY, twin];
    vi.stubGlobal("fetch", mockFetch([RENAMED, WIDGET_B]));
    renderDashboard();

    await screen.findByText("Jobs");
    await screen.findByText("Hosts");
    expect(screen.getAllByText("Jobs")).toHaveLength(1);
    for (const saved of puts) expect(saved).toHaveLength(3);
    expect(layoutItems).toHaveLength(3);
  });

  it("bei mehreren früheren Kennungen gilt die Reihenfolge der Liste", async () => {
    const mid = { ...OLD_ENTRY, ext_id: "mid", w: 4, h: 2, config: { aus: "mid" } };
    layoutItems = [OLD_ENTRY, mid];
    vi.stubGlobal("fetch", mockFetch([{ ...RENAMED, legacy_ext_ids: ["mid", "old"] }]));
    renderDashboard();

    await screen.findByText("Jobs");
    expect(await savedWithRenamed()).toEqual([OLD_ENTRY, mid, { ...mid, ext_id: "renamed" }]);
  });

  it("nimmt aus mehreren Einträgen der alten Erweiterung den zum Widget gehörenden", async () => {
    const andere = { widget_id: "andere", ext_id: "old", x: 6, y: 0, w: 4, h: 2, config: { aus: "andere" } };
    layoutItems = [andere, OLD_ENTRY];
    vi.stubGlobal("fetch", mockFetch([RENAMED]));
    renderDashboard();

    await screen.findByText("Jobs");
    expect(await savedWithRenamed()).toEqual([andere, OLD_ENTRY, { ...OLD_ENTRY, ext_id: "renamed" }]);
  });

  it("nimmt keinen Eintrag mit gleicher widget_id von einer fremden Erweiterung (nicht in der Liste)", async () => {
    const fremd = { ...OLD_ENTRY, ext_id: "fremd" };
    layoutItems = [fremd];
    vi.stubGlobal("fetch", mockFetch([RENAMED]));
    renderDashboard();

    await screen.findByText("Jobs");
    // Neu platziert wie jedes neue Widget: ganz links, unter allem anderen, ohne die Einstellungen des fremden Eintrags.
    expect(await savedWithRenamed()).toEqual([fremd, { widget_id: "summary", ext_id: "renamed", x: 0, y: 3, w: 2, h: 2, config: {} }]);
  });

  it("ohne `legacy_ext_ids` im Katalog (keine Umbenennung, älteres Backend) wird ein neues Widget wie bisher neu platziert", async () => {
    layoutItems = [OLD_ENTRY];
    const { legacy_ext_ids: _unused, ...withoutLegacy } = RENAMED;
    vi.stubGlobal("fetch", mockFetch([withoutLegacy]));
    renderDashboard();

    await screen.findByText("Jobs");
    expect(await savedWithRenamed()).toEqual([OLD_ENTRY, { widget_id: "summary", ext_id: "renamed", x: 0, y: 3, w: 2, h: 2, config: {} }]);
  });
});

describe("Widget-Mindestgrößen (Nutzer: 'die Widgets sind kacke')", () => {
  it("sizeFor hebt zu kleine Karten auf die Mindestgröße ihres Typs", async () => {
    const { sizeFor } = await import("./DashboardPage");
    expect(sizeFor("list", 2, 2)).toEqual({ w: 4, h: 3 });
    expect(sizeFor("stat", 2, 2)).toEqual({ w: 2, h: 2 });
    // Größere bleiben, wie der Nutzer sie gezogen hat.
    expect(sizeFor("list", 6, 4)).toEqual({ w: 6, h: 4 });
    expect(sizeFor("unbekannt", 1, 1)).toEqual({ w: 2, h: 2 });
  });

  it("packLayout füllt dicht von links oben, in der gegebenen Reihenfolge, ohne Überlappung", async () => {
    const { packLayout } = await import("./DashboardPage");
    const pos = packLayout([
      { i: "a", w: 4, h: 3 },
      { i: "b", w: 4, h: 3 },
      { i: "c", w: 4, h: 3 },
      { i: "d", w: 3, h: 2 },
      { i: "e", w: 6, h: 3 },
    ]);
    expect(Object.fromEntries(pos)).toEqual({
      a: { x: 0, y: 0 }, b: { x: 4, y: 0 }, c: { x: 8, y: 0 }, d: { x: 0, y: 3 }, e: { x: 3, y: 3 },
    });
  });
});

describe("DashboardPage Leerzustände", () => {
  it("ohne jedes Widget im Katalog: erklärt, woher Widgets kommen, mit Link zu den Modulen (nur mit Recht)", async () => {
    layoutItems = [];
    useAuthStore.setState({
      user: { id: "u1", username: "a", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["extensions.manage"] },
    });
    vi.stubGlobal("fetch", mockFetch([]));
    renderDashboard();
    const empty = await screen.findByTestId("dashboard-no-widgets");
    expect(empty.textContent).toContain("Noch keine Widgets");
    expect(within(empty).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
  });

  it("ohne Recht extensions.manage nur der Text, kein Link", async () => {
    layoutItems = [];
    useAuthStore.setState({
      user: { id: "u2", username: "v", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["hosts.read"] },
    });
    vi.stubGlobal("fetch", mockFetch([]));
    renderDashboard();
    const empty = await screen.findByTestId("dashboard-no-widgets");
    expect(empty.textContent).toContain("Administrator");
    expect(within(empty).queryByRole("link")).toBeNull();
  });

  it("der Widget-Dialog ohne Widgets zeigt ebenfalls den Hinweis statt einer leeren Liste", async () => {
    layoutItems = [];
    useAuthStore.setState({
      user: { id: "u1", username: "a", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    });
    vi.stubGlobal("fetch", mockFetch([]));
    renderDashboard();
    await screen.findByTestId("dashboard-no-widgets");
    const dialog = await openPicker();
    expect(dialog.getByText("Noch keine Widgets")).toBeInTheDocument();
    expect(dialog.getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
  });
});
