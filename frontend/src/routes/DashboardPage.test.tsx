import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
/** `meta` der Antwort des Backup-Widgets; `{ state: "not_configured" }` blendet es aus. */
let backupsMeta: Record<string, unknown>;
/** Jede gespeicherte Fassung des Layouts (PUT), die erste ist die der Auto-Platzierung. */
let puts: unknown[][];
let layoutGroups: unknown[] | undefined;
/** Jede gespeicherte Fassung der Gruppen (PUT nur mit `groups`). */
let groupPuts: unknown[][];
/** Antwort des Proxmox-Widgets. */
let proxmoxData: Record<string, unknown>;

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
        JSON.stringify([{ id: "layout-1", name: "Standard", is_default: true, items: layoutItems, groups: layoutGroups, created_at: "", updated_at: "" }]),
        { status: 200 },
      );
    }
    if (url.endsWith("/api/v1/dashboard/layouts/layout-1") && method === "PUT") {
      const body = JSON.parse(init!.body as string);
      if (body.items) {
        layoutItems = body.items;
        puts.push(body.items);
      }
      if (body.groups) {
        layoutGroups = body.groups;
        groupPuts.push(body.groups);
      }
      return new Response(
        JSON.stringify({ id: "layout-1", name: "Standard", is_default: true, items: layoutItems, groups: layoutGroups, created_at: "", updated_at: "" }),
        { status: 200 },
      );
    }
    if (url.endsWith("/api/v1/ext/backups/widgets/summary") && method === "GET") {
      return new Response(JSON.stringify({ data: { count: 2 }, meta: backupsMeta }), { status: 200 });
    }
    if (url.endsWith("/api/v1/ext/renamed/widgets/summary") && method === "GET") {
      return new Response(JSON.stringify({ data: { count: 5 }, meta: {} }), { status: 200 });
    }
    if (url.endsWith("/api/v1/ext/proxmox/widgets/overview") && method === "GET") {
      return new Response(JSON.stringify({ data: proxmoxData, meta: {} }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
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
  backupsMeta = {};
  layoutGroups = undefined;
  groupPuts = [];
  proxmoxData = { count: 7 };
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

  it("quetscht Karten nicht und scrollt nicht waagerecht: unter 960px gestapelt in zwei Spalten", async () => {
    // Halbes Fenster (~930 px)
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 930 });
    try {
      vi.stubGlobal("fetch", mockFetch());
      renderDashboard();
      const card = await screen.findByText("Jobs");
      expect(card.closest(".react-grid-item")).toBeNull();
      expect(screen.getByTestId("widget-stack")).toHaveClass("sm:grid-cols-2");
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, "clientWidth", original);
    }
  });

  it("ab 960px wieder das Raster, w=2-Karte dort ~146px", async () => {
    const width = await cardWidthForContainer(960);
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

describe("Neue Kacheln nach dem Zwischenspeicher", () => {
  const WIDGET_C = { ...WIDGET_A, id: "status-keller", title: "Keller", data_endpoint: "widgets/summary" };

  it("platziert eine Kachel, die erst der frische Katalog kennt, auch wenn zuerst der Zwischenspeicher gezeigt wurde", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const page = () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/"]}><DashboardPage /></MemoryRouter>
      </QueryClientProvider>
    );
    vi.stubGlobal("fetch", mockFetch([WIDGET_A, WIDGET_B]));
    const first = render(page());
    await screen.findByText("Jobs");
    first.unmount();
    expect(puts).toEqual([]);

    // Zurück auf die Startseite: zuerst der alte Katalog aus dem Zwischenspeicher, danach der frische mit der neuen Kachel.
    vi.stubGlobal("fetch", mockFetch([WIDGET_A, WIDGET_B, WIDGET_C]));
    render(page());
    await waitFor(() => expect(puts).toHaveLength(1));
    expect((puts[0] as { widget_id: string }[]).map((i) => i.widget_id)).toEqual(["summary", "overview", "status-keller"]);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(puts).toHaveLength(1);
  });
});

describe("Widget-Mindestgrößen (Nutzer: 'die Widgets sind kacke')", () => {
  it("gridLayoutFor packt eine Gruppe dicht von links, auch wenn ihre gespeicherten Plätze Lücken lassen", async () => {
    const { gridLayoutFor } = await import("./DashboardPage");
    const entry = (widget_id: string, x: number, y: number) => ({
      item: { widget_id, ext_id: "e", x, y, w: 4, h: 3, config: {} }, size: { w: 4, h: 3 }, min: { w: 4, h: 3 },
    });
    // Gespeichert in voller Breite, die übrigen Plätze gehören zu anderen Gruppen.
    const layout = gridLayoutFor([entry("b", 8, 0), entry("a", 4, 0), entry("c", 8, 3)]);
    expect(layout.map(({ i, x, y }) => [i, x, y])).toEqual([["e:b", 4, 0], ["e:a", 0, 0], ["e:c", 8, 0]]);
  });

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

describe("Höhe nach Inhalt: gespeichert werden nur Lage und Breite", () => {
  const item = (widget_id: string, x: number, y: number, w: number, h: number) => ({ widget_id, ext_id: "e", x, y, w, h, config: {} });

  it("rowsFor: gemessen vor Vorgabe; Zahl und Tacho bis zur Messung S, sonst die Mindesthöhe, höchstens L", async () => {
    const { rowsFor } = await import("./DashboardPage");
    expect(rowsFor("list", 1, 3)).toBe(1);
    expect(rowsFor("stat", undefined, 4)).toBe(2);
    expect(rowsFor("list", undefined, 3)).toBe(3);
    expect(rowsFor("list", undefined, 9)).toBe(5);
  });

  it("placeItems übernimmt x, y und w aus dem Raster, die gespeicherte Höhe bleibt", async () => {
    const { placeItems } = await import("./DashboardPage");
    const items = [item("a", 0, 0, 4, 2), item("b", 4, 0, 4, 2)];
    const next = placeItems(items, [
      { i: "e:a", x: 4, y: 3, w: 6, h: 5 },
      { i: "e:b", x: 4, y: 0, w: 4, h: 1 },
    ]);
    expect(next).toEqual([item("a", 4, 3, 6, 2), item("b", 4, 0, 4, 2)]);
  });

  it("placeItems: nur andere Höhen (gemessen) -- dieselben Einträge zurück, also kein PUT", async () => {
    const { placeItems } = await import("./DashboardPage");
    const items = [item("a", 0, 0, 4, 2)];
    expect(placeItems(items, [{ i: "e:a", x: 0, y: 0, w: 4, h: 5 }])).toBe(items);
  });
});

describe("DashboardPage: nicht eingerichtete Module", () => {
  it("blendet das Widget aus; „Widgets verwalten“ zeigt den Hinweis mit Link zu den Einstellungen", async () => {
    backupsMeta = { state: "not_configured" };
    useAuthStore.setState({
      user: { id: "u1", username: "a", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    });
    vi.stubGlobal("fetch", mockFetch());
    renderDashboard();
    expect(await screen.findByText("Hosts")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Jobs")).not.toBeInTheDocument());
    expect(screen.queryByText("Backup-Center", { selector: "h3" })).not.toBeInTheDocument();

    const dialog = await openPicker();
    const hint = dialog.getByTestId("widget-not-configured-backups:summary");
    expect(hint.textContent).toContain("Nicht eingerichtet");
    expect(within(hint).getByRole("link", { name: "Einrichten" })).toHaveAttribute("href", "/settings/extensions/backups");
    // Bleibt im Layout (Häkchen gesetzt), nichts gespeichert.
    expect(dialog.getByRole("checkbox", { name: /Backup-Center/ })).toBeChecked();
    expect(puts).toHaveLength(0);
  });

  it("ohne Recht extensions.manage nur der Hinweis, kein Link", async () => {
    backupsMeta = { state: "not_configured" };
    useAuthStore.setState({
      user: { id: "u2", username: "v", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["hosts.read"] },
    });
    vi.stubGlobal("fetch", mockFetch());
    renderDashboard();
    await screen.findByText("Hosts");
    const dialog = await openPicker();
    const hint = await dialog.findByTestId("widget-not-configured-backups:summary");
    expect(hint.textContent).toContain("Administrator");
    expect(within(hint).queryByRole("link")).toBeNull();
  });

  it("andere Zustände (z. B. unreachable) blenden nichts aus", async () => {
    backupsMeta = { state: "unreachable" };
    vi.stubGlobal("fetch", mockFetch());
    renderDashboard();
    expect(await screen.findByText("Jobs")).toBeInTheDocument();
  });

  it("sind alle Widgets nicht eingerichtet: Hinweis statt leerer Fläche", async () => {
    backupsMeta = { state: "not_configured" };
    vi.stubGlobal("fetch", mockFetch([WIDGET_A]));
    layoutItems = [{ widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} }];
    renderDashboard();
    const empty = await screen.findByTestId("dashboard-all-not-configured");
    expect(empty.textContent).toContain("Noch nichts eingerichtet");
    expect(screen.queryByTestId("dashboard-no-visible-widgets")).not.toBeInTheDocument();
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

describe("DashboardPage: Gruppen", () => {
  const BACKUPS = { ...WIDGET_A, group: "backups" };
  const PROXMOX = { ...WIDGET_B, group: "servers", view: { ...WIDGET_B.view, tone: "{{ tone }}" } };

  it("zeigt die Widgets unter ihren Gruppen, in der Reihenfolge der Vorgabe", async () => {
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    renderDashboard();
    await screen.findByText("Jobs");
    const sections = screen.getAllByTestId(/^widget-group-/).map((el) => el.dataset.testid);
    expect(sections).toEqual(["widget-group-servers", "widget-group-backups"]);
    expect(within(screen.getByTestId("widget-group-servers")).getByText("Hosts")).toBeInTheDocument();
    expect(within(screen.getByTestId("widget-group-backups")).getByRole("button", { name: /Backups/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("zuklappen versteckt die Widgets der Gruppe und speichert nur die Gruppen", async () => {
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    renderDashboard();
    await screen.findByText("Jobs");
    const before = puts.length;
    fireEvent.click(within(screen.getByTestId("widget-group-backups")).getByRole("button", { name: /Backups/ }));
    await waitFor(() => expect(groupPuts).toHaveLength(1));
    expect(screen.queryByText("Jobs")).toBeNull();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
    expect(groupPuts[0]).toContainEqual({ key: "backups", title: null, collapsed: true });
    expect(groupPuts[0].map((g) => (g as { key: string }).key)).toEqual(["servers", "security", "apps", "backups", "services"]);
    expect(puts).toHaveLength(before);
  });

  it("zwei Gruppen schnell hintereinander zuklappen: beide bleiben zu", async () => {
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    renderDashboard();
    await screen.findByText("Jobs");
    const backups = within(screen.getByTestId("widget-group-backups")).getByRole("button", { name: /Backups/ });
    const servers = within(screen.getByTestId("widget-group-servers")).getByRole("button", { name: /Server und Proxmox/ });
    act(() => {
      backups.click();
      servers.click();
    });
    await waitFor(() => expect(groupPuts).toHaveLength(2));
    const collapsed = (groupPuts[1] as { key: string; collapsed: boolean }[]).filter((g) => g.collapsed).map((g) => g.key);
    expect(collapsed).toEqual(["servers", "backups"]);
    expect(screen.queryByText("Jobs")).toBeNull();
    expect(screen.queryByText("Hosts")).toBeNull();
  });

  it("nach dem Speichern gilt wieder der Stand vom Server, auch eine Änderung von einem anderen Gerät", async () => {
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    const { queryClient } = renderDashboard();
    await screen.findByText("Jobs");
    fireEvent.click(within(screen.getByTestId("widget-group-backups")).getByRole("button", { name: /Backups/ }));
    await waitFor(() => expect(groupPuts).toHaveLength(1));
    expect(screen.queryByText("Jobs")).toBeNull();

    layoutGroups = [{ key: "backups", title: null, collapsed: false }];
    await act(() => queryClient.invalidateQueries({ queryKey: ["dashboard-layouts"] }));
    expect(await screen.findByText("Jobs")).toBeInTheDocument();
    expect(within(screen.getByTestId("widget-group-backups")).getByRole("button", { name: /Backups/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("eine zugeklappte Gruppe zeigt einen Warnpunkt, wenn darin etwas nicht stimmt", async () => {
    layoutGroups = [{ key: "servers", title: null, collapsed: true }];
    proxmoxData = { count: 7, tone: "danger" };
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    renderDashboard();
    await screen.findByText("Jobs");
    const servers = within(screen.getByTestId("widget-group-servers"));
    expect(await servers.findByTestId("group-attention")).toHaveTextContent("Störung");
    expect(servers.queryByText("Hosts")).toBeNull();
    expect(within(screen.getByTestId("widget-group-backups")).queryByTestId("group-attention")).toBeNull();
  });

  it("aufgeklappt kein Punkt, auch bei einer Störung (die Karten zeigen sie selbst)", async () => {
    proxmoxData = { count: 7, tone: "danger" };
    const fetchMock = mockFetch([BACKUPS, PROXMOX]);
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    await screen.findByText("Hosts");
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/widgets/overview"))).toBe(true));
    expect(screen.queryByTestId("group-attention")).toBeNull();
  });

  it("ein Widget, das nicht lädt, gibt der zugeklappten Gruppe einen grauen Punkt statt Ruhe", async () => {
    layoutGroups = [{ key: "servers", title: null, collapsed: true }];
    const base = mockFetch([BACKUPS, PROXMOX]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/widgets/overview")) return new Response("{}", { status: 500 });
      return base(input, init);
    }));
    renderDashboard();
    await screen.findByText("Jobs");
    const dot = await within(screen.getByTestId("widget-group-servers")).findByTestId("group-attention");
    expect(dot).toHaveTextContent("Nicht geladen");
  });

  it("ohne Warnung kein Punkt, auch zugeklappt", async () => {
    layoutGroups = [{ key: "servers", title: null, collapsed: true }];
    const fetchMock = mockFetch([BACKUPS, PROXMOX]);
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    await screen.findByText("Jobs");
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/widgets/overview"))).toBe(true));
    const servers = within(screen.getByTestId("widget-group-servers"));
    expect(servers.getByRole("button", { name: /Server und Proxmox/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("group-attention")).toBeNull();
  });

  it("eine selbst gewählte Gruppe (config.group) geht vor; ein Widget ohne Gruppe steht unter „Dienste“", async () => {
    layoutItems = [
      { widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} },
      { widget_id: "overview", ext_id: "proxmox", x: 2, y: 0, w: 2, h: 2, config: { group: "security" } },
    ];
    vi.stubGlobal("fetch", mockFetch([WIDGET_A, PROXMOX]));
    renderDashboard();
    await screen.findByText("Jobs");
    expect(within(screen.getByTestId("widget-group-security")).getByText("Hosts")).toBeInTheDocument();
    expect(within(screen.getByTestId("widget-group-services")).getByText("Jobs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sicherheit/ })).toBeInTheDocument();
  });

  it("ein eigener Gruppenname aus dem Layout steht im Kopf", async () => {
    layoutGroups = [{ key: "backups", title: "Sicherungen", collapsed: false }];
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    renderDashboard();
    await screen.findByText("Jobs");
    expect(within(screen.getByTestId("widget-group-backups")).getByRole("button", { name: /Sicherungen/ })).toBeInTheDocument();
    expect(screen.getAllByTestId(/^widget-group-/).map((el) => el.dataset.testid)).toEqual(["widget-group-backups", "widget-group-servers"]);
  });
});

describe("DashboardPage: Ladefehler statt Leerzustand", () => {
  it("scheitert die Liste der Widgets, steht der Grund mit „Erneut versuchen“ da, nicht „Noch keine Widgets“", async () => {
    const base = mockFetch();
    let fail = true;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (fail && url.endsWith("/api/v1/widgets")) return new Response(JSON.stringify({ detail: "Dienst startet neu" }), { status: 503 });
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    const error = await screen.findByTestId("load-error");
    expect(error).toHaveTextContent("Die Liste der Widgets ließ sich gerade nicht laden");
    expect(screen.queryByTestId("dashboard-no-widgets")).toBeNull();

    fail = false;
    fireEvent.click(within(error).getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.queryByTestId("load-error")).toBeNull());
  });

  it("scheitert die Anordnung, steht der Grund da statt „Kein Dashboard-Layout gefunden“", async () => {
    const base = mockFetch();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/v1/dashboard/layouts") && (init?.method ?? "GET") === "GET") {
        return new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status: 500 });
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Anordnung der Widgets ließ sich gerade nicht laden");
    expect(screen.queryByText("Kein Dashboard-Layout gefunden.")).toBeNull();
  });
});

describe("DashboardPage: Bearbeiten", () => {
  const BACKUPS = { ...WIDGET_A, group: "backups" };
  const PROXMOX = { ...WIDGET_B, group: "servers" };
  const card = (id: string) => screen.getByTestId(`widget-${id}`);

  function setup(catalog: unknown[] = [BACKUPS, PROXMOX]) {
    const fetchMock = mockFetch(catalog);
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    return fetchMock;
  }
  /** Alle gesendeten PUT-Bodies, in der Reihenfolge. */
  const putBodies = (fetchMock: ReturnType<typeof mockFetch>) =>
    fetchMock.mock.calls
      .filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT")
      .map(([, init]) => JSON.parse((init as RequestInit).body as string) as { items?: { widget_id: string; w: number; config: Record<string, unknown> }[]; groups?: { key: string; title: string | null; collapsed: boolean }[] });
  async function startEditing() {
    await screen.findByText("Jobs");
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  }
  const finish = () => fireEvent.click(screen.getByRole("button", { name: "Fertig" }));

  it("außerhalb des Bearbeitens: kein Griff, keine Leiste, kein Ziehen der Breite", async () => {
    setup();
    await screen.findByText("Jobs");
    expect(document.querySelector(".widget-drag-handle")).toBeNull();
    // react-grid-layout lässt die Breitengriffe im Dokument und versteckt sie per Klasse.
    const items = document.querySelectorAll(".react-grid-item");
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) expect(item).toHaveClass("react-resizable-hide");
    expect(screen.queryAllByTestId("widget-edit-bar")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Fertig" })).toBeNull();
  });

  it("„Bearbeiten“ zeigt an jeder Karte Griff und Leiste, dazu den Hinweis; Abbrechen und Fertig ersetzen den Knopf", async () => {
    setup();
    await startEditing();
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(screen.getByRole("button", { name: "Abbrechen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fertig" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Widgets verwalten" })).toBeInTheDocument();
    expect(screen.getAllByTitle("Zum Verschieben ziehen")).toHaveLength(2);
    expect(screen.getAllByTestId("widget-edit-bar")).toHaveLength(2);
    for (const item of document.querySelectorAll(".react-grid-item")) expect(item).not.toHaveClass("react-resizable-hide");
    expect(screen.getByTestId("edit-hint")).toHaveTextContent("Ziehe Widgets am Griff");
    const bar = within(card("backups-summary"));
    expect(bar.getByLabelText("Höhe")).toHaveValue("auto");
    expect(bar.getByRole("button", { name: "Schmal" })).toHaveAttribute("aria-pressed", "true");
    expect(bar.getByLabelText("Gruppe")).toHaveValue("backups");
  });

  it("den Knopf „Bearbeiten“ gibt es nur, solange ein Widget auf der Startseite steht", async () => {
    setup();
    expect(await screen.findByRole("button", { name: "Bearbeiten" })).toBeInTheDocument();
    cleanup();

    layoutItems = (layoutItems as { config: object }[]).map((i) => ({ ...i, config: { hidden: true } }));
    setup();
    await screen.findByText(/Keine Widgets auf dem Dashboard/);
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(screen.getByRole("button", { name: "Widgets verwalten" })).toBeInTheDocument();
  });

  it("Höhe „Groß“ wählen und „Fertig“: genau ein PUT mit den Einträgen, ohne Gruppen, danach endet der Modus", async () => {
    const fetchMock = setup();
    await startEditing();
    fireEvent.change(within(card("backups-summary")).getByLabelText("Höhe"), { target: { value: "l" } });
    expect(putBodies(fetchMock)).toHaveLength(0);

    finish();
    await waitFor(() => expect(screen.getByRole("button", { name: "Bearbeiten" })).toBeInTheDocument());
    const bodies = putBodies(fetchMock);
    expect(bodies).toHaveLength(1);
    expect(bodies[0].items!.find((i) => i.widget_id === "summary")!.config.size).toBe("l");
    expect(bodies[0].items!.find((i) => i.widget_id === "overview")!.config.size).toBeUndefined();
    expect(bodies[0]).not.toHaveProperty("groups");
    expect(screen.queryAllByTestId("widget-edit-bar")).toHaveLength(0);
  });

  it("eine Kachel, die erst während des Bearbeitens dazukommt, stellt die Seite nach „Fertig“ dazu", async () => {
    const KELLER = { ...BACKUPS, id: "status-keller", title: "Keller" };
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    const { queryClient } = renderDashboard();
    await startEditing();
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX, KELLER]));
    await queryClient.invalidateQueries({ queryKey: ["widgets"] });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(puts).toEqual([]);

    finish();
    await waitFor(() => expect(visible().map((i) => i.widget_id)).toEqual(["summary", "overview", "status-keller"]));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(visible().map((i) => i.widget_id)).toEqual(["summary", "overview", "status-keller"]);
  });

  it("nach „Fertig“ gilt für die Gruppen wieder der Stand vom Server, auch eine Änderung von einem anderen Gerät", async () => {
    vi.stubGlobal("fetch", mockFetch([BACKUPS, PROXMOX]));
    const { queryClient } = renderDashboard();
    await startEditing();
    fireEvent.change(screen.getByLabelText("Name der Gruppe Server und Proxmox"), { target: { value: "Maschinen" } });
    finish();
    expect(await screen.findByRole("button", { name: /Maschinen/ })).toBeInTheDocument();

    layoutGroups = [{ key: "servers", title: "Rechner", collapsed: false }];
    await act(() => queryClient.invalidateQueries({ queryKey: ["dashboard-layouts"] }));
    expect(await screen.findByRole("button", { name: /Rechner/ })).toBeInTheDocument();
  });

  // Für das Layout gibt es keinen Kanal und kein Nachladen beim Fokus: Was ein anderes Gerät im Modus speichert, steht
  // nur auf dem Server (`layoutGroups`), die Seite kennt es nicht.
  it("klappt ein anderes Gerät im Modus eine Gruppe zu, bleibt sie nach „Fertig“ zu", async () => {
    setup();
    await startEditing();
    fireEvent.change(within(card("backups-summary")).getByLabelText("Höhe"), { target: { value: "l" } });
    layoutGroups = [{ key: "backups", title: null, collapsed: true }];

    finish();
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(groupPuts).toHaveLength(0);
    expect((puts[0] as { config: object }[])[0].config).toEqual({ size: "l" });
    await waitFor(() => expect(screen.queryByText("Jobs")).toBeNull());
  });

  it("benennt ein anderes Gerät im Modus eine Gruppe um, die hier unberührt bleibt, gilt nach „Fertig“ der neue Name", async () => {
    layoutGroups = [{ key: "servers", title: "Maschinen", collapsed: false }];
    setup();
    await startEditing();
    fireEvent.change(screen.getByLabelText("Name der Gruppe Backups"), { target: { value: "Sicherungen" } });
    layoutGroups = [{ key: "servers", title: "Rechner", collapsed: false }];

    finish();
    await waitFor(() => expect(groupPuts).toHaveLength(1));
    expect(groupPuts[0]).toContainEqual({ key: "servers", title: "Rechner", collapsed: false });
    expect(groupPuts[0]).toContainEqual({ key: "backups", title: "Sicherungen", collapsed: false });
    expect(await screen.findByRole("button", { name: /Rechner/ })).toBeInTheDocument();
  });

  it("benennen beide Geräte dieselbe Gruppe um, gewinnt der Name aus dem Modus, das Zuklappen vom anderen Gerät bleibt", async () => {
    setup();
    await startEditing();
    fireEvent.change(screen.getByLabelText("Name der Gruppe Backups"), { target: { value: "Sicherungen" } });
    layoutGroups = [{ key: "backups", title: "Ablage", collapsed: false }, { key: "servers", title: null, collapsed: true }];

    finish();
    await waitFor(() => expect(groupPuts).toHaveLength(1));
    expect(groupPuts[0]).toContainEqual({ key: "backups", title: "Sicherungen", collapsed: false });
    expect(groupPuts[0]).toContainEqual({ key: "servers", title: null, collapsed: true });
    expect(await screen.findByRole("button", { name: /Sicherungen/ })).toBeInTheDocument();
  });

  it("zurück auf „Automatisch“ nimmt die Größe wieder aus dem Eintrag", async () => {
    layoutItems = [{ widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: { size: "m" } }, layoutItems[1]];
    const fetchMock = setup();
    await startEditing();
    const select = within(card("backups-summary")).getByLabelText("Höhe");
    expect(select).toHaveValue("m");
    fireEvent.change(select, { target: { value: "auto" } });
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    expect(putBodies(fetchMock)[0].items![0].config).toEqual({});
  });

  it("„Abbrechen“ verwirft alles ohne Anfrage; beim nächsten Bearbeiten steht der alte Stand da", async () => {
    const fetchMock = setup();
    await startEditing();
    const bar = within(card("backups-summary"));
    fireEvent.change(bar.getByLabelText("Höhe"), { target: { value: "l" } });
    fireEvent.click(bar.getByRole("button", { name: "Breit" }));
    fireEvent.change(bar.getByLabelText("Gruppe"), { target: { value: "security" } });
    fireEvent.change(screen.getByLabelText("Name der Gruppe Server und Proxmox"), { target: { value: "Maschinen" } });
    fireEvent.click(within(card("proxmox-overview")).getByRole("button", { name: "Entfernen" }));
    expect(screen.queryByText("Hosts")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(putBodies(fetchMock)).toHaveLength(0);
    expect(await screen.findByText("Hosts")).toBeInTheDocument();
    expect(within(screen.getByTestId("widget-group-backups")).getByText("Jobs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Server und Proxmox/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    const again = within(card("backups-summary"));
    expect(again.getByLabelText("Höhe")).toHaveValue("auto");
    expect(again.getByRole("button", { name: "Schmal" })).toHaveAttribute("aria-pressed", "true");
    expect(layoutItems).toEqual([
      { widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} },
      { widget_id: "overview", ext_id: "proxmox", x: 2, y: 0, w: 2, h: 2, config: {} },
    ]);
  });

  it("Gruppe wechseln: die Karte steht sofort unter der neuen Gruppe, nach „Fertig“ steht sie im PUT", async () => {
    const fetchMock = setup();
    await startEditing();
    fireEvent.change(within(card("backups-summary")).getByLabelText("Gruppe"), { target: { value: "security" } });
    expect(await within(screen.getByTestId("widget-group-security")).findByText("Jobs")).toBeInTheDocument();
    expect(screen.queryByTestId("widget-group-backups")).toBeNull();
    expect(putBodies(fetchMock)).toHaveLength(0);

    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const moved = putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary")!;
    expect(moved.config.group).toBe("security");
  });

  it("eine Karte, die in eine Gruppe mit anderen kommt, steht dort am Ende", async () => {
    layoutItems = [
      { widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} },
      { widget_id: "overview", ext_id: "proxmox", x: 0, y: 4, w: 2, h: 2, config: { group: "services" } },
    ];
    const fetchMock = setup();
    await startEditing();
    fireEvent.change(within(card("backups-summary")).getByLabelText("Gruppe"), { target: { value: "services" } });
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const items = putBodies(fetchMock)[0].items as unknown as { widget_id: string; y: number }[];
    expect(items.find((i) => i.widget_id === "summary")!.y).toBeGreaterThan(items.find((i) => i.widget_id === "overview")!.y);
  });

  it("Breite: „Breit“ setzt 8 Spalten, „Schmal“ wieder die Mindestbreite des Typs (mindestens 4)", async () => {
    const fetchMock = setup();
    await startEditing();
    const bar = within(card("backups-summary"));
    fireEvent.click(bar.getByRole("button", { name: "Breit" }));
    expect(bar.getByRole("button", { name: "Breit" })).toHaveAttribute("aria-pressed", "true");
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    expect(putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary")!.w).toBe(8);

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(within(card("backups-summary")).getByRole("button", { name: "Schmal" }));
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(2));
    expect(putBodies(fetchMock)[1].items!.find((i) => i.widget_id === "summary")!.w).toBe(4);
  });

  it("ein Klick auf die schon gewählte Breite ändert nichts, auch nicht bei einer Karte unter der Schmal-Breite", async () => {
    const fetchMock = setup();
    await startEditing();
    const narrow = within(card("backups-summary")).getByRole("button", { name: "Schmal" });
    expect(narrow).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(narrow);
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    expect(putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary")!.w).toBe(2);
  });

  it("ein unveränderter Gruppenname wird als Vorgabe gespeichert, nicht als eigener Name", async () => {
    const fetchMock = setup();
    await startEditing();
    expect(screen.getByLabelText("Name der Gruppe Server und Proxmox")).toHaveValue("Server und Proxmox");
    fireEvent.change(screen.getByLabelText("Name der Gruppe Backups"), { target: { value: "Sicherungen" } });
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    for (const group of putBodies(fetchMock)[0].groups!) expect(group.title).toBe(group.key === "backups" ? "Sicherungen" : null);
  });

  it("im Modus ist der Inhalt jeder Karte inert, also auch per Tastatur nicht bedienbar; danach wieder normal", async () => {
    setup();
    await startEditing();
    const bodies = () => [...document.querySelectorAll(".widget-body")];
    expect(bodies().length).toBe(2);
    for (const body of bodies()) expect(body).toHaveAttribute("inert");
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => {
      for (const body of bodies()) expect(body).not.toHaveAttribute("inert");
    });
  });

  it("eine umbenannte Gruppe, deren letzte Karte wegzieht, behält ihren Namen im PUT", async () => {
    const fetchMock = setup();
    await startEditing();
    fireEvent.change(screen.getByLabelText("Name der Gruppe Backups"), { target: { value: "Sicherungen" } });
    fireEvent.change(within(card("backups-summary")).getByLabelText("Gruppe"), { target: { value: "security" } });
    expect(screen.queryByTestId("widget-group-backups")).toBeNull();
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    expect(putBodies(fetchMock)[0].groups!.find((g) => g.key === "backups")!.title).toBe("Sicherungen");
    expect(putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary")!.config.group).toBe("security");
  });

  it("Entfernen: die Karte ist sofort weg, nach „Fertig“ steht config.hidden im PUT", async () => {
    const fetchMock = setup();
    await startEditing();
    fireEvent.click(within(card("backups-summary")).getByRole("button", { name: "Entfernen" }));
    expect(screen.queryByText("Jobs")).toBeNull();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
    expect(putBodies(fetchMock)).toHaveLength(0);

    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const removed = putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary")!;
    expect(removed.config.hidden).toBe(true);
    expect(screen.queryByText("Jobs")).toBeNull();
  });

  it("nach dem Gruppenwechsel bleibt der Fokus im Gruppen-Feld der Karte, auch beim nächsten Wechsel", async () => {
    setup();
    await startEditing();
    const select = within(card("backups-summary")).getByLabelText("Gruppe");
    select.focus();
    fireEvent.change(select, { target: { value: "security" } });
    await within(screen.getByTestId("widget-group-security")).findByText("Jobs");
    const moved = within(card("backups-summary")).getByLabelText("Gruppe");
    expect(moved).not.toBe(select);
    expect(document.activeElement).toBe(moved);

    fireEvent.change(moved, { target: { value: "apps" } });
    await within(screen.getByTestId("widget-group-apps")).findByText("Jobs");
    expect(document.activeElement).toBe(within(card("backups-summary")).getByLabelText("Gruppe"));
  });

  it("nach „Entfernen“ steht der Fokus auf dem Hinweis, ohne dass die Seite dorthin springt", async () => {
    setup();
    await startEditing();
    const remove = within(card("backups-summary")).getByRole("button", { name: "Entfernen" });
    const hint = screen.getByTestId("edit-hint");
    const focusHint = vi.spyOn(hint, "focus");
    remove.focus();
    fireEvent.click(remove);
    expect(screen.queryByText("Jobs")).toBeNull();
    expect(document.activeElement).toBe(hint);
    expect(focusHint).toHaveBeenCalledWith({ preventScroll: true });
  });

  it("Gruppe umbenennen: der Name steht nach „Fertig“ im PUT und im Kopf; ein leeres Feld heißt wieder Vorgabe", async () => {
    layoutGroups = [{ key: "servers", title: "Maschinen", collapsed: false }];
    const fetchMock = setup();
    await startEditing();
    const backups = screen.getByLabelText("Name der Gruppe Backups");
    expect(backups).toHaveValue("Backups");
    expect(backups).toHaveAttribute("placeholder", "Backups");
    expect(backups).toHaveAttribute("maxlength", "60");
    const servers = screen.getByLabelText("Name der Gruppe Server und Proxmox");
    expect(servers).toHaveValue("Maschinen");

    fireEvent.change(backups, { target: { value: "  Sicherungen " } });
    fireEvent.change(servers, { target: { value: "" } });
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const groups = putBodies(fetchMock)[0].groups!;
    expect(groups.find((g) => g.key === "backups")!.title).toBe("Sicherungen");
    expect(groups.find((g) => g.key === "servers")!.title).toBeNull();
    expect(await screen.findByRole("button", { name: /Sicherungen/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Server und Proxmox/ })).toBeInTheDocument();
  });

  it("zugeklappte Gruppen stehen im Modus offen, ohne Zuklappen; der gespeicherte Zustand bleibt", async () => {
    layoutGroups = [{ key: "backups", title: null, collapsed: true }];
    const fetchMock = setup();
    await screen.findByText("Hosts");
    expect(screen.queryByText("Jobs")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    expect(await screen.findByText("Jobs")).toBeInTheDocument();
    for (const section of screen.getAllByTestId(/^widget-group-/)) expect(section.querySelector("[aria-expanded]")).toBeNull();

    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    expect(putBodies(fetchMock)[0]).not.toHaveProperty("groups");
    await waitFor(() => expect(screen.queryByText("Jobs")).toBeNull());
  });

  it("im Modus löst nichts ein PUT aus, auch „Widgets verwalten“ ändert nur den Entwurf", async () => {
    const fetchMock = setup();
    await startEditing();
    const dialog = await openPicker();
    fireEvent.click((await dialog.findByText("Backup-Center")).closest("label")!.querySelector("input")!);
    expect(putBodies(fetchMock)).toHaveLength(0);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(screen.queryByText("Jobs")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(await screen.findByText("Jobs")).toBeInTheDocument();
    expect(putBodies(fetchMock)).toHaveLength(0);
  });

  it("scheitert das Speichern, bleibt der Modus samt Entwurf, mit Hinweis; ein zweiter Versuch klappt", async () => {
    const base = mockFetch([BACKUPS, PROXMOX]);
    let failNext = true;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT" && failNext) {
        failNext = false;
        return new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status: 500 });
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    await startEditing();
    fireEvent.change(within(card("backups-summary")).getByLabelText("Höhe"), { target: { value: "m" } });

    finish();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Speichern hat nicht geklappt");
    expect(alert).toHaveTextContent("Datenbank gesperrt");
    expect(within(card("backups-summary")).getByLabelText("Höhe")).toHaveValue("m");
    expect(screen.getByRole("button", { name: "Fertig" })).toBeEnabled();

    finish();
    await waitFor(() => expect(screen.getByRole("button", { name: "Bearbeiten" })).toBeInTheDocument());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(puts).toHaveLength(1);
  });

  it("während „Fertig“ speichert, ruht der Entwurf: keine Änderung per Tastatur, kein zweites Speichern", async () => {
    const base = mockFetch([BACKUPS, PROXMOX]);
    let release: () => void = () => undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") await new Promise<void>((resolve) => { release = resolve; });
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();
    await startEditing();
    const height = within(card("backups-summary")).getByLabelText("Höhe");
    fireEvent.change(height, { target: { value: "m" } });

    finish();
    await waitFor(() => expect(screen.getByRole("button", { name: "Fertig" })).toBeDisabled());
    expect(screen.getByTestId("edit-hint").parentElement?.querySelector("[aria-busy='true']")).toHaveAttribute("inert");
    fireEvent.change(height, { target: { value: "l" } });
    fireEvent.click(screen.getByRole("button", { name: "Fertig" }));
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: "Bearbeiten" })).toBeInTheDocument());
    expect(putBodies(fetchMock)).toHaveLength(1);
    expect(putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary")!.config.size).toBe("m");
    expect(screen.getByRole("button", { name: "Bearbeiten" })).toHaveFocus();
  });

  it("„Widgets verwalten“ im Modus: der Dialog sagt, dass erst „Fertig“ speichert, und heißt „Schließen“", async () => {
    setup();
    await startEditing();
    expect(screen.getByTestId("edit-hint")).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Widgets verwalten" }));
    expect(await screen.findByTestId("picker-draft-hint")).toHaveTextContent("erst mit „Fertig“ auf der Startseite");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Schließen" })).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "Fertig" })).toBeNull();
  });

  it("eine breite Karte rechts rückt beim Speichern so weit nach links, dass sie ins Raster passt", async () => {
    layoutItems = [
      { widget_id: "summary", ext_id: "backups", x: 6, y: 0, w: 4, h: 2, config: {} },
      { widget_id: "overview", ext_id: "proxmox", x: 0, y: 0, w: 4, h: 2, config: { group: "backups" } },
    ];
    const fetchMock = setup();
    await startEditing();
    fireEvent.click(within(card("backups-summary")).getByRole("button", { name: "Breit" }));
    finish();
    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const saved = putBodies(fetchMock)[0].items!.find((i) => i.widget_id === "summary") as unknown as { x: number; w: number };
    expect(saved.w).toBe(8);
    expect(saved.x + saved.w).toBeLessThanOrEqual(12);
  });

  it("am Handy dieselbe Leiste, aber ohne Griff und mit passendem Hinweis", async () => {
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 390 });
    try {
      setup();
      await startEditing();
      expect(screen.getAllByTestId("widget-edit-bar")).toHaveLength(2);
      expect(document.querySelector(".widget-drag-handle")).toBeNull();
      expect(screen.queryByRole("button", { name: "Breit" })).toBeNull();
      expect(screen.getByTestId("edit-hint")).toHaveTextContent("größeren Bildschirm");
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, "clientWidth", original);
    }
  });

  it("rowsFor: eine gewählte Größe gilt vor der Messung, Zahl und Tacho bleiben mindestens S", async () => {
    const { rowsFor } = await import("./DashboardPage");
    expect(rowsFor("list", 3, 3, 5)).toBe(5);
    expect(rowsFor("list", undefined, 3, 2)).toBe(2);
    expect(rowsFor("stat", 1, 2, 2)).toBe(2);
    expect(rowsFor("list", 1, 3, null)).toBe(1);
  });
});
