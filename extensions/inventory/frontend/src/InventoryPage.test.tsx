import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InventoryPage, inventoryCsv, warrantyState } from "./InventoryPage";

const CATEGORIES = [{ id: "cat-1", name: "Elektronik" }];
const LOCATIONS = [{ id: "loc-1", name: "Keller", parent_id: null }];
const ITEMS = [
  {
    id: "item-1", name: "Bohrmaschine", description: "Bosch", category_id: "cat-1", location_id: "loc-1",
    quantity: 2, purchase_date: "2024-01-15", purchase_price_cents: 8999, warranty_until: "2099-01-01",
    notes: "Im Regal", images: [],
  },
];

function mockFetch(overrides: { items?: unknown[]; onCreate?: (body: unknown) => void } = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.match(/\/images\/[^/]+$/) && method === "GET") {
      return new Response("png", { status: 200, headers: { "Content-Type": "image/png" } });
    }
    if (url.includes("/ext/inventory/items") && method === "GET") {
      return new Response(JSON.stringify(overrides.items ?? ITEMS), { status: 200 });
    }
    if (url.endsWith("/ext/inventory/categories") && method === "GET") {
      return new Response(JSON.stringify(CATEGORIES), { status: 200 });
    }
    if (url.endsWith("/ext/inventory/locations") && method === "GET") {
      return new Response(JSON.stringify(LOCATIONS), { status: 200 });
    }
    if (url.endsWith("/ext/inventory/items") && method === "POST") {
      const body = JSON.parse(init?.body as string);
      overrides.onCreate?.(body);
      return new Response(JSON.stringify({ ...body, id: "item-2", images: [] }), { status: 201 });
    }
    if (url.endsWith("/ext/inventory/categories") && method === "POST") {
      const body = JSON.parse(init?.body as string);
      return new Response(JSON.stringify({ id: "cat-2", ...body }), { status: 201 });
    }
    if (url.match(/\/items\/[^/]+$/) && method === "PUT") {
      const body = JSON.parse(init?.body as string);
      return new Response(JSON.stringify({ ...ITEMS[0], ...body, images: [] }), { status: 200 });
    }
    if (url.match(/\/items\/[^/]+$/) && method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (url.includes("/images") && method === "POST") {
      return new Response(JSON.stringify({ id: "img-1", filename: "img-1.png", content_type: "image/png", size_bytes: 3, url: "/api/v1/ext/inventory/items/item-1/images/img-1" }), { status: 201 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

beforeEach(() => {
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("InventoryPage", () => {
  it("zeigt geladene Gegenstände mit Kategorie/Standort/Menge", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<InventoryPage />);

    await screen.findByText("Bohrmaschine");
    expect(screen.getByText(/Elektronik.*Keller.*Menge 2/)).toBeInTheDocument();
  });

  it("legt einen neuen Gegenstand ueber das Formular an", async () => {
    let created: unknown = null;
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => (created = body) }));
    render(<InventoryPage />);

    await screen.findByText("Bohrmaschine");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Gegenstand" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Akkuschrauber" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await waitFor(() => expect(created).not.toBeNull());
    expect((created as { name: string }).name).toBe("Akkuschrauber");
  });

  it("fragt vor dem Loeschen eines Gegenstands ueber den echten Dialog nach", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await screen.findByText("Bohrmaschine");
    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));

    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Bohrmaschine"), expect.anything()));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/items/item-1"), expect.objectContaining({ method: "DELETE" })));
  });

  it("oeffnet das Bearbeiten-Formular vorbefuellt mit den Werten des Gegenstands", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<InventoryPage />);

    await screen.findByText("Bohrmaschine");
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));

    expect(screen.getByLabelText("Name")).toHaveValue("Bohrmaschine");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeInTheDocument();
  });

  it("legt eine neue Kategorie ueber das Taxonomie-Panel an", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await screen.findByText("Bohrmaschine");
    fireEvent.click(screen.getByRole("button", { name: /Kategorien & Standorte/ }));
    fireEvent.change(screen.getByPlaceholderText("Neue Kategorie"), { target: { value: "Werkzeug" } });
    fireEvent.click(screen.getByRole("button", { name: "Kategorie hinzufügen" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/ext/inventory/categories"),
        expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Werkzeug" }) }),
      ),
    );
  });

  it("laedt ein Bild fuer einen aufgeklappten Gegenstand hoch", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<InventoryPage />);

    await screen.findByText("Bohrmaschine");
    fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));

    const file = new File(["bild"], "foto.png", { type: "image/png" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/items/item-1/images"), expect.objectContaining({ method: "POST" })),
    );
  });

  it("zeigt Fotos ueber die ausgelieferte URL mit Login-Token an", async () => {
    const realCreate = URL.createObjectURL;
    const realRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:foto");
    URL.revokeObjectURL = vi.fn();
    try {
      const image = { id: "img-1", filename: "img-1.png", content_type: "image/png", size_bytes: 3, url: "/api/v1/ext/inventory/items/item-1/images/img-1" };
      const fetchMock = mockFetch({ items: [{ ...ITEMS[0], images: [image] }] });
      vi.stubGlobal("fetch", fetchMock);
      const { container } = render(<InventoryPage />);

      await screen.findByText("Bohrmaschine");
      fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));

      // Vorschaubild in der Liste + Foto in den Details, beide als Blob-URL.
      await waitFor(() => expect(container.querySelectorAll('img[src="blob:foto"]')).toHaveLength(2));
      const imageCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes("/images/img-1"));
      expect(imageCalls.length).toBeGreaterThanOrEqual(2);
      for (const [input, init] of imageCalls) {
        expect(input).toBe("/api/v1/ext/inventory/items/item-1/images/img-1");
        expect(new Headers((init as RequestInit).headers).get("Authorization")).toBe("Bearer tok");
      }
      cleanup();
    } finally {
      URL.createObjectURL = realCreate;
      URL.revokeObjectURL = realRevoke;
    }
  });

  it("zeigt einen Hinweis, wenn keine Gegenstände erfasst sind", async () => {
    vi.stubGlobal("fetch", mockFetch({ items: [] }));
    render(<InventoryPage />);

    await screen.findByText(/Noch keine Gegenstände erfasst/);
  });

  it("Zusammenfassung mit Gesamtwert (Menge x Preis) und Filter nach Garantie", async () => {
    const items = [
      ITEMS[0],
      { ...ITEMS[0], id: "item-3", name: "Alter Router", quantity: 1, purchase_price_cents: 5000, warranty_until: "2001-01-01", category_id: null },
    ];
    vi.stubGlobal("fetch", mockFetch({ items }));
    render(<InventoryPage />);
    const summary = await screen.findByTestId("inventory-summary");
    // 2 x 89,99 + 1 x 50,00 = 229,98
    expect(summary.textContent).toContain("2 Gegenstand/Gegenstände");
    expect(summary.textContent).toContain("229,98");
    expect(summary.textContent).toContain("1 abgelaufen");
    fireEvent.change(screen.getByLabelText("Nach Garantie filtern"), { target: { value: "expired" } });
    await waitFor(() => expect(screen.queryByText("Bohrmaschine")).toBeNull());
    expect(screen.getByText("Alter Router")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Nach Garantie filtern"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Nach Kategorie filtern"), { target: { value: "cat-1" } });
    await waitFor(() => expect(screen.queryByText("Alter Router")).toBeNull());
  });

  it("Garantie-Zustand und CSV fuer Excel", () => {
    const today = new Date("2026-09-25T12:00:00");
    expect(warrantyState(null, today)).toBe("none");
    expect(warrantyState("2026-09-24", today)).toBe("expired");
    expect(warrantyState("2026-12-01", today)).toBe("soon");
    expect(warrantyState("2027-12-01", today)).toBe("active");
    const csv = inventoryCsv([{ ...ITEMS[0], notes: 'Regal "oben"; links' }], () => "Elektronik", () => "Keller");
    const [header, row] = csv.replace(/^\ufeff/, "").split("\r\n");
    expect(csv.startsWith("\ufeff")).toBe(true);
    expect(header).toBe("Name;Kategorie;Standort;Menge;Kaufdatum;Preis (EUR);Garantie bis;Beschreibung;Notizen");
    expect(row).toBe('Bohrmaschine;Elektronik;Keller;2;2024-01-15;89,99;2099-01-01;Bosch;"Regal ""oben""; links"');
  });
});
