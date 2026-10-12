import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { InventoryPage, itemPayload, priceLinks, priceQuery, rackRows } from "./InventoryPage";

/** Inventar als Homelab-Baukasten: Technik-Angaben, Server-Bezug, Bauprojekte und Preisvergleich per Link. */

const ITEMS = [
  {
    id: "i-disk", name: "Festplatte", description: null, category_id: null, location_id: null, quantity: 2,
    purchase_date: null, purchase_price_cents: 11000, warranty_until: null, notes: null, images: [],
    manufacturer: "WD", model: "Red Plus 4TB", serial_number: "WX12345", product_url: "https://example.com/wd-red",
    host_id: "h-nas", project_id: "p-nas",
  },
  {
    id: "i-cable", name: "Patchkabel", description: null, category_id: null, location_id: null, quantity: 5,
    purchase_date: null, purchase_price_cents: null, warranty_until: null, notes: null, images: [],
  },
];
const PROJECTS = [
  { id: "p-nas", name: "NAS bauen", status: "building", description: null, budget_cents: 20000, item_count: 1, spent_cents: 22000 },
];
const HOSTS = [{ id: "h-nas", display_name: "nas" }, { id: "h-pi", display_name: "Raspberry Pi" }];

const RACK = { id: "r-1", name: "Keller", height_units: 6, location_id: null, notes: null };
const SWITCH = { ...ITEMS[1], id: "i-switch", name: "Switch", rack_id: "r-1", rack_position: 5, rack_units: 1 };

let calls: { method: string; url: string; body?: unknown }[];
let projectsFail: boolean;
let racks: typeof RACK[];
let placeFails: boolean;

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET" && url.includes("/ext/inventory/items?rack_id=r-1")) return json([SWITCH]);
    if (method === "GET" && url.includes("/ext/inventory/items")) return json(ITEMS);
    if (method === "GET" && url.endsWith("/ext/inventory/racks")) return json(racks);
    if (method === "POST" && url.endsWith("/ext/inventory/racks")) {
      const rack = { ...RACK, ...body, id: "r-new" };
      racks = [...racks, rack];
      return json(rack, 201);
    }
    if (method === "PUT" && /\/items\/[^/]+$/.test(url) && placeFails) {
      return json({ detail: "Der Platz ist schon belegt: „Switch“ steckt dort (HE 5–5)." }, 409);
    }
    if (method === "GET" && url.endsWith("/ext/inventory/categories")) return json([]);
    if (method === "GET" && url.endsWith("/ext/inventory/locations")) return json([]);
    if (method === "GET" && url.endsWith("/ext/inventory/projects")) {
      if (projectsFail) return json({ detail: "Projekte gesperrt" }, 500);
      return json(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/projects/p-nas")) ? [] : PROJECTS);
    }
    if (method === "GET" && url.endsWith("/hosts")) return json(HOSTS);
    if (method === "POST" && url.endsWith("/ext/inventory/items")) return json({ ...body, id: "i-new", images: [] }, 201);
    if (method === "PUT" && /\/items\/[^/]+$/.test(url)) return json({ ...ITEMS[0], ...body });
    if (method === "POST" && url.endsWith("/ext/inventory/projects")) return json({ ...body, id: "p-new", item_count: 0, spent_cents: 0 }, 201);
    if (method === "PUT" && /\/projects\/[^/]+$/.test(url)) return json({ ...PROJECTS[0], ...body });
    if (method === "DELETE" && /\/projects\/[^/]+$/.test(url)) return new Response(null, { status: 204 });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string, options?: { danger?: boolean }) => Promise<boolean>>;

beforeEach(() => {
  calls = [];
  projectsFail = false;
  racks = [RACK];
  placeFails = false;
  confirmDialog = vi.fn<(message: string, options?: { danger?: boolean }) => Promise<boolean>>().mockResolvedValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", mockFetch());
});

describe("Preisvergleich", () => {
  it("sucht nach Hersteller und Modell, sonst nach dem Namen -- nur ein Link, kein Auslesen", () => {
    expect(priceQuery({ name: "Festplatte", manufacturer: "WD", model: "Red Plus 4TB" })).toBe("WD Red Plus 4TB");
    expect(priceQuery({ name: "Festplatte", manufacturer: "WD", model: null })).toBe("Festplatte");
    expect(priceQuery({ name: "  Switch 8 Port ", manufacturer: null, model: undefined })).toBe("Switch 8 Port");
    expect(priceLinks({ name: "x", manufacturer: "WD", model: "Red Plus 4TB" })).toEqual([
      { label: "Geizhals", href: "https://geizhals.de/?fs=WD%20Red%20Plus%204TB" },
      { label: "idealo", href: "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=WD%20Red%20Plus%204TB" },
    ]);
  });
});

describe("Technik-Angaben", () => {
  it("Details zeigen Hersteller, Modell, Seriennummer, Server und Projekt, dazu Produkt- und Vergleichslinks", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    // In der Zeile: Hersteller und Modell statt der Kategorie, dazu der Server.
    expect(screen.getByText("Festplatte").nextSibling?.textContent).toContain("WD Red Plus 4TB");
    await waitFor(() => expect(screen.getByText("Festplatte").nextSibling?.textContent).toContain("nas"));

    fireEvent.click(screen.getAllByRole("button", { name: "Details anzeigen" })[0]);
    expect(await screen.findByText("WX12345")).toBeInTheDocument();
    expect(screen.getAllByText("NAS bauen").some((el) => el.tagName === "DD")).toBe(true);
    const links = within(screen.getByTestId("links-i-disk")).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["Zum Produkt", "Geizhals", "idealo"]);
    for (const a of links) {
      expect(a).toHaveAttribute("target", "_blank");
      expect(a.getAttribute("rel")).toContain("noopener");
    }
    expect(links[0]).toHaveAttribute("href", "https://example.com/wd-red");
  });

  it("Formular: Technik-Felder werden mitgeschickt, beim Bearbeiten vorbefüllt und gleich aufgeklappt", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getAllByRole("button", { name: /Bearbeiten/ })[0]);
    const tech = screen.getByTestId("tech-fields");
    expect(tech).toHaveAttribute("open");
    expect(within(tech).getByLabelText("Modell")).toHaveValue("Red Plus 4TB");
    await waitFor(() => expect(within(tech).getByLabelText("Gehört zu Server")).toHaveValue("h-nas"));
    fireEvent.change(within(tech).getByLabelText("Seriennummer"), { target: { value: "WX99999" } });
    fireEvent.change(within(tech).getByLabelText("Gehört zu Server"), { target: { value: "h-pi" } });
    fireEvent.change(within(tech).getByLabelText("Bauprojekt"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toMatchObject({
      manufacturer: "WD", model: "Red Plus 4TB", serial_number: "WX99999", product_url: "https://example.com/wd-red",
      host_id: "h-pi", project_id: null,
    });
  });

  it("neuer Gegenstand: Technik-Abschnitt zu, leere Felder gehen als null raus", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Neuer Gegenstand/ }));
    expect(screen.getByTestId("tech-fields")).not.toHaveAttribute("open");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Lüfter" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/items"))).toBe(true));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/items"))!;
    expect(post.body).toMatchObject({ name: "Lüfter", manufacturer: null, model: null, host_id: null, project_id: null });
  });
});

describe("Bauprojekte", () => {
  it("zeigt Teile, Ausgaben und Budget, filtert die Liste und legt neue an", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Bauprojekte/ }));
    const row = await screen.findByTestId("project-p-nas");
    expect(row.textContent).toContain("im Bau");
    expect(row.textContent).toContain("1 Teil");
    expect(row.textContent).toMatch(/220,00\s€ ausgegeben von 200,00\s€/);
    expect(within(row).getByText("über Budget")).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: "Teile zeigen" }));
    await waitFor(() => expect(screen.queryByText("Patchkabel")).toBeNull());
    expect(screen.getByText("Festplatte")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Teile zeigen" }));
    expect(await screen.findByText("Patchkabel")).toBeInTheDocument();

    fireEvent.change(within(row).getByLabelText("Status von NAS bauen"), { target: { value: "done" } });
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/projects/p-nas"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ name: "NAS bauen", status: "done", description: null, budget_cents: 20000 });

    fireEvent.change(screen.getByLabelText("Name des Bauprojekts"), { target: { value: "Rack aufräumen" } });
    fireEvent.change(screen.getByLabelText("Budget in Euro"), { target: { value: "49,5".replace(",", ".") } });
    fireEvent.click(within(screen.getByTestId("projects").parentElement!).getByRole("button", { name: /Anlegen/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/projects"))).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/projects"))!.body).toEqual({ name: "Rack aufräumen", budget_cents: 4950 });
  });

  it("Entfernen fragt nach und sagt, dass die Teile bleiben", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Bauprojekte/ }));
    fireEvent.click(await screen.findByRole("button", { name: "NAS bauen entfernen" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(confirmDialog.mock.calls[0][0]).toContain("Die Teile bleiben im Inventar");
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/projects/p-nas"))).toBe(true));
  });

  it("kommen die Projekte nicht an, bleibt das Inventar benutzbar und der Grund steht bei den Projekten", async () => {
    projectsFail = true;
    render(<InventoryPage />);
    expect(await screen.findByText("Festplatte")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Bauprojekte/ }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Liste der Bauprojekte ließ sich gerade nicht laden: Projekte gesperrt");
  });
});

describe("Racks", () => {
  it("Belegung von oben nach unten, Geräte über ihre Höhe", () => {
    const item = (id: string, rack_position: number, rack_units: number) => ({ ...ITEMS[1], id, name: id, rack_position, rack_units });
    const rows = rackRows(6, [item("server", 2, 2), item("switch", 6, 1)]);
    expect(rows.map((r) => (r.kind === "device" ? `${r.item.name}:${r.bottom}-${r.top}` : `frei:${r.unit}`))).toEqual([
      "switch:6-6", "frei:5", "frei:4", "server:2-3", "frei:1",
    ]);
  });

  it("Änderungen am Platz schicken die alten Felder vollständig mit, die übrigen neuen nicht", () => {
    const payload = itemPayload(ITEMS[0] as never, { rack_id: "r-1", rack_position: 3, rack_units: 2 });
    expect(payload).toMatchObject({ name: "Festplatte", quantity: 2, purchase_price_cents: 11000, rack_id: "r-1", rack_position: 3, rack_units: 2 });
    expect(payload).not.toHaveProperty("host_id"); // bleibt beim Server, wie es ist
    expect(itemPayload(ITEMS[0] as never, { rack_id: null })).toMatchObject({ rack_id: null });
  });

  it("Gerät in eine freie HE einsetzen und wieder herausnehmen; ein belegter Platz meldet den Grund", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Racks/ }));
    const rack = await screen.findByTestId("rack-r-1");
    // 6 HE: Switch in HE 5, Rest frei.
    expect(within(rack).getAllByRole("listitem").map((li) => li.textContent?.replace(/\s+/g, " ").trim().slice(0, 10))).toEqual([
      "HE 6frei", "HE 5Switch", "HE 4frei", "HE 3frei", "HE 2frei", "HE 1frei",
    ]);

    fireEvent.click(within(rack).getByRole("button", { name: "Gerät in HE 3 einsetzen" }));
    fireEvent.change(within(rack).getByLabelText("Gerät"), { target: { value: "i-disk" } });
    fireEvent.change(within(rack).getByLabelText("Höhe in HE"), { target: { value: "2" } });
    fireEvent.click(within(rack).getByRole("button", { name: "Einsetzen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/items/i-disk"))).toBe(true));
    // Eingesetzt in HE 3 mit 2 HE: das Gerät reicht von HE 2 bis 3.
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ name: "Festplatte", rack_id: "r-1", rack_position: 2, rack_units: 2 });

    fireEvent.click(within(screen.getByTestId("rack-device-i-switch")).getByRole("button", { name: "Herausnehmen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/items/i-switch"))).toBe(true));
    expect(calls.find((c) => c.method === "PUT" && c.url.endsWith("/items/i-switch"))!.body).toMatchObject({
      name: "Switch", rack_id: null, rack_position: null, rack_units: null,
    });

    placeFails = true;
    fireEvent.click(within(rack).getByRole("button", { name: "Gerät in HE 4 einsetzen" }));
    fireEvent.change(within(rack).getByLabelText("Gerät"), { target: { value: "i-disk" } });
    fireEvent.click(within(rack).getByRole("button", { name: "Einsetzen" }));
    expect(await screen.findByText("Fehler: Der Platz ist schon belegt: „Switch“ steckt dort (HE 5–5).")).toBeInTheDocument();
  });

  it("neues Rack anlegen und gleich auswählen", async () => {
    racks = [];
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Racks/ }));
    expect(await screen.findByText("Noch kein Rack.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name des Racks"), { target: { value: "Wohnzimmer" } });
    fireEvent.change(screen.getByLabelText("Höhe des Racks"), { target: { value: "12" } });
    fireEvent.click(within(screen.getByLabelText("Name des Racks").closest("form")!).getByRole("button", { name: /Anlegen/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/racks"))).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/racks"))!.body).toEqual({ name: "Wohnzimmer", height_units: 12 });
    expect(await screen.findByRole("button", { name: "Wohnzimmer · 12 HE", pressed: true })).toBeInTheDocument();
  });
});

describe("Nachbesserungen", () => {
  it("nach dem Entfernen des gefilterten Projekts ist die Liste wieder vollständig", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Bauprojekte/ }));
    fireEvent.click(within(await screen.findByTestId("project-p-nas")).getByRole("button", { name: "Teile zeigen" }));
    await waitFor(() => expect(screen.queryByText("Patchkabel")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "NAS bauen entfernen" }));
    expect(await screen.findByText("Patchkabel")).toBeInTheDocument();
  });

  it("Einsetzen ganz unten mit zu viel Höhe: verständlicher Hinweis statt Serverfehler", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Racks/ }));
    const rack = await screen.findByTestId("rack-r-1");
    fireEvent.click(within(rack).getByRole("button", { name: "Gerät in HE 1 einsetzen" }));
    fireEvent.change(within(rack).getByLabelText("Gerät"), { target: { value: "i-disk" } });
    fireEvent.change(within(rack).getByLabelText("Höhe in HE"), { target: { value: "2" } });
    fireEvent.click(within(rack).getByRole("button", { name: "Einsetzen" }));
    expect(await screen.findByText("Unter HE 1 ist nicht genug Platz für 2 HE. Wähle eine kleinere Höhe oder eine höhere HE.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("die Rack-Belegung lädt neu, wenn sich die Liste ändert (z. B. Gerät gelöscht)", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Racks/ }));
    await screen.findByTestId("rack-r-1");
    const before = calls.filter((c) => c.url.includes("rack_id=r-1")).length;
    fireEvent.change(screen.getByLabelText("Suchen"), { target: { value: "Fest" } }); // lädt die Liste neu
    await waitFor(() => expect(calls.filter((c) => c.url.includes("rack_id=r-1")).length).toBeGreaterThan(before));
    // Die Suche ändert die Belegung nicht: der Switch steht weiter drin.
    expect(screen.getByTestId("rack-device-i-switch")).toBeInTheDocument();
  });

  it("Technik-Abschnitt klappt nicht zu, wenn man das letzte Feld leert", async () => {
    render(<InventoryPage />);
    await screen.findByText("Festplatte");
    fireEvent.click(screen.getByRole("button", { name: /Neuer Gegenstand/ }));
    const tech = screen.getByTestId("tech-fields") as HTMLDetailsElement;
    tech.open = true;
    fireEvent(tech, new Event("toggle"));
    fireEvent.change(within(tech).getByLabelText("Modell"), { target: { value: "X" } });
    fireEvent.change(within(tech).getByLabelText("Modell"), { target: { value: "" } });
    expect(tech).toHaveAttribute("open");
  });
});
