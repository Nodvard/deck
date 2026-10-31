import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InventoryPage } from "./InventoryPage";

/** Teile eines Servers auslesen und Bestellliste einlesen: auswählen, ankreuzen, übernehmen. */

const HOSTS = [
  { id: "h-nas", display_name: "nas", os_family: "linux", credential: { id: "c1" } },
  { id: "h-win", display_name: "game-win", os_family: "windows", credential: { id: "c2" } },
  { id: "h-new", display_name: "neu", os_family: "linux", credential: null },
];
const SCAN = {
  host_id: "h-nas", host_name: "nas", virtual: false, missing: [],
  suggestions: [
    { key: "cpu", kind: "Prozessor", name: "AMD Ryzen 7 5700G", manufacturer: "AMD", model: "AMD Ryzen 7 5700G", serial_number: null, quantity: 1, detail: "8 Kerne", preselect: true },
    { key: "disk:sda:WX1", kind: "Festplatte", name: "Festplatte 4 TB (WDC WD40EFZX)", manufacturer: "WD", model: "WDC WD40EFZX", serial_number: "WX1", quantity: 1, detail: "/dev/sda", preselect: true },
    { key: "memory", kind: "Arbeitsspeicher", name: "Arbeitsspeicher 64 GB", manufacturer: null, model: null, serial_number: null, quantity: 1, detail: null, hint: "Summe aller Riegel", preselect: true },
    { key: "pci:0", kind: "Grafikkarte", name: "Grafikkarte AMD Cezanne", manufacturer: "AMD", model: "Cezanne", serial_number: null, quantity: 1, detail: null, hint: "kann auch auf dem Mainboard sitzen", preselect: false },
  ],
};

let calls: { method: string; url: string; body?: unknown }[];
let scanAnswer: { status: number; body: unknown };
let failOn: Set<string>;
let items: unknown[];
let itemsFail: boolean;
let permissions: Set<string>;

beforeEach(() => {
  calls = [];
  scanAnswer = { status: 200, body: SCAN };
  failOn = new Set();
  // Gefiltert wird auf der Seite nicht: „schon im Inventar“ kommt aus der ganzen Liste (Seriennummer, klein geschrieben).
  items = [{ id: "i-old", name: "Alte Platte", serial_number: "wx1", product_url: "https://example.com/switch", images: [], quantity: 1 }];
  itemsFail = false;
  permissions = new Set(["hosts.execute"]);
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(),
    hasPermission: vi.fn((p: string) => permissions.has(p) || p.startsWith("inventory.")),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET" && url.includes("/ext/inventory/items")) return itemsFail ? json({ detail: "kaputt" }, 500) : json(items);
    if (method === "GET" && url.endsWith("/ext/inventory/categories")) return json([{ id: "k1", name: "Server" }]);
    if (method === "GET" && url.endsWith("/ext/inventory/projects")) return json([]);
    if (method === "GET" && url.endsWith("/ext/inventory/locations")) return json([{ id: "l1", name: "Keller", parent_id: null }]);
    if (method === "GET" && url.endsWith("/hosts")) return json(HOSTS);
    if (method === "POST" && url.endsWith("/scan")) return json(scanAnswer.body, scanAnswer.status);
    if (method === "POST" && url.endsWith("/ext/inventory/items")) {
      if (failOn.has((body as { name: string }).name)) return json({ detail: "Datenbank gesperrt" }, 500);
      const created = { ...body, id: `i-${calls.length}`, images: [] };
      items = [...items, created];
      return json(created, 201);
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
});

async function openMenuEntry(label: string) {
  render(<InventoryPage />);
  // Steht unter „Mehr“ (erst da, wenn die Serverliste geladen ist).
  const more = await screen.findByTestId("inventory-more");
  await screen.findByText("Alte Platte");
  fireEvent.click(within(more).getByText("Mehr"));
  fireEvent.click(await within(more).findByRole("button", { name: label }));
}

const posts = () => calls.filter((c) => c.method === "POST" && c.url.endsWith("/ext/inventory/items")).map((c) => c.body as Record<string, unknown>);

describe("Teile auslesen", () => {
  it("nur Linux-Server mit SSH-Zugang sind wählbar, der erste davon ist vorgewählt", async () => {
    await openMenuEntry("Teile eines Servers auslesen");
    const select = screen.getByLabelText("Server zum Auslesen") as HTMLSelectElement;
    expect(select.value).toBe("h-nas");
    const options = [...select.options].map((o) => [o.textContent, o.disabled]);
    expect(options).toEqual([
      ["Server wählen …", false], ["nas", false], ["game-win (nur Linux)", true], ["neu (ohne SSH-Zugang)", true],
    ]);
  });

  it("ohne das Recht, Befehle auf Servern auszuführen, gibt es den Menüpunkt nicht", async () => {
    permissions = new Set();
    render(<InventoryPage />);
    const more = await screen.findByTestId("inventory-more");
    await screen.findByText("Alte Platte");
    fireEvent.click(within(more).getByText("Mehr"));
    expect(within(more).queryByRole("button", { name: "Teile eines Servers auslesen" })).toBeNull();
    expect(within(more).getByRole("button", { name: "Bestellliste einlesen (CSV)" })).toBeInTheDocument();
  });

  it("vorab angekreuzt: was sicher ein Teil ist und noch fehlt; übernommen wird nur das Angekreuzte, mit Server, Kategorie und Standort", async () => {
    await openMenuEntry("Teile eines Servers auslesen");
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    const result = await screen.findByTestId("scan-result");
    expect(within(result).getByLabelText("AMD Ryzen 7 5700G übernehmen")).toBeChecked();
    // Gleiche Seriennummer wie ein Gegenstand im Inventar (andere Schreibweise): nicht angekreuzt, mit Hinweis.
    expect(within(result).getByLabelText("Festplatte 4 TB (WDC WD40EFZX) übernehmen (schon im Inventar)")).not.toBeChecked();
    expect(within(result).getByText("schon im Inventar")).toBeInTheDocument();
    // Karten können auf dem Mainboard sitzen: nicht vorab angekreuzt.
    expect(within(result).getByLabelText("Grafikkarte AMD Cezanne übernehmen")).not.toBeChecked();
    expect(result.textContent).toContain("Seriennummer WX1");
    expect(result.textContent).toContain("Arbeitsspeicher · Summe aller Riegel");

    fireEvent.click(within(result).getByText("Arbeitsspeicher 64 GB")); // die ganze Zeile ist anklickbar: abwählen
    fireEvent.change(within(result).getByLabelText("Kategorie für die neuen Teile"), { target: { value: "k1" } });
    fireEvent.change(within(result).getByLabelText("Standort für die neuen Teile"), { target: { value: "l1" } });
    fireEvent.click(within(result).getByRole("button", { name: "1 Teil übernehmen" }));

    expect(await screen.findByText("1 Teil übernommen.")).toBeInTheDocument();
    expect(posts()).toEqual([{
      name: "AMD Ryzen 7 5700G", quantity: 1, description: "8 Kerne", manufacturer: "AMD", model: "AMD Ryzen 7 5700G",
      serial_number: null, host_id: "h-nas", category_id: "k1", location_id: "l1", project_id: null,
    }]);
    expect(screen.queryByTestId("scan-result")).toBeNull();

    // Noch einmal auslesen: der übernommene Prozessor (ohne Seriennummer) gilt jetzt als vorhanden.
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    const again = await screen.findByTestId("scan-result");
    expect(within(again).getByLabelText("AMD Ryzen 7 5700G übernehmen (schon im Inventar)")).not.toBeChecked();
    expect(within(again).getByLabelText("Arbeitsspeicher 64 GB übernehmen")).toBeChecked();
  });

  it("zwei gleiche Platten mit verschiedenen Seriennummern: nur die erfasste gilt als vorhanden", async () => {
    items = [...items, { id: "i-raid", name: "SSD 1 TB (Samsung 870)", serial_number: "S1", host_id: "h-nas", images: [], quantity: 1 }];
    const disk = (serial: string) => ({
      key: `disk:sd${serial}:${serial}`, kind: "SSD", name: "SSD 1 TB (Samsung 870)", manufacturer: "Samsung", model: "Samsung 870",
      serial_number: serial, quantity: 1, detail: null, hint: null, preselect: true,
    });
    scanAnswer = { status: 200, body: { ...SCAN, suggestions: [disk("S1"), disk("S2")] } };
    await openMenuEntry("Teile eines Servers auslesen");
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    const result = await screen.findByTestId("scan-result");
    const boxes = within(result).getAllByRole("checkbox");
    expect(boxes.map((b) => [b.getAttribute("aria-label"), (b as HTMLInputElement).checked])).toEqual([
      ["SSD 1 TB (Samsung 870) übernehmen (schon im Inventar)", false],
      ["SSD 1 TB (Samsung 870) übernehmen", true],
    ]);
  });

  it("lässt sich die Gegenstandsliste nicht laden, ist nichts vorab angekreuzt und das steht da", async () => {
    await openMenuEntry("Teile eines Servers auslesen");
    itemsFail = true;
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    const result = await screen.findByTestId("scan-result");
    expect(result.textContent).toContain("Konnte nicht prüfen, was schon im Inventar steht.");
    expect(within(result).getByRole("button", { name: "0 Teile übernehmen" })).toBeDisabled();
  });

  it("eine VM oder ein Container wird erklärt, fehlende Programme auch; ein Fehler beim Auslesen steht mit Grund da", async () => {
    scanAnswer = { status: 200, body: { ...SCAN, virtual: true, missing: ["lspci"], suggestions: [] } };
    await openMenuEntry("Teile eines Servers auslesen");
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    expect(await screen.findByText(/ist eine virtuelle Maschine oder ein Container/)).toBeInTheDocument();
    expect(screen.getByText(/Lies am besten den Wirt aus/)).toBeInTheDocument();
    expect(screen.getByText(/lspci \(Paket „pciutils“\) fehlt/)).toBeInTheDocument();
    expect(screen.getByText("Nichts gefunden, was sich als Teil anbietet.")).toBeInTheDocument();

    scanAnswer = { status: 502, body: { detail: "Der Server ließ sich nicht auslesen: Verbindung abgelehnt – läuft der SSH-Dienst?" } };
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    expect(await screen.findByText("Fehler: Der Server ließ sich nicht auslesen: Verbindung abgelehnt – läuft der SSH-Dienst?")).toBeInTheDocument();
  });

  it("scheitert ein Teil: die übrigen kommen trotzdem rein, nichts wird beim zweiten Versuch doppelt angelegt", async () => {
    failOn = new Set(["AMD Ryzen 7 5700G"]);
    await openMenuEntry("Teile eines Servers auslesen");
    fireEvent.click(screen.getByRole("button", { name: "Auslesen" }));
    fireEvent.click(await screen.findByRole("button", { name: "2 Teile übernehmen" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("1 übernommen. 1 Teil ging nicht.");
    expect(alert.textContent).toContain("„AMD Ryzen 7 5700G“: Datenbank gesperrt");
    // Der Arbeitsspeicher kam nach dem Fehler trotzdem an und ist abgehakt.
    expect(posts().map((b) => b.name)).toEqual(["AMD Ryzen 7 5700G", "Arbeitsspeicher 64 GB"]);
    const memory = screen.getByLabelText("Arbeitsspeicher 64 GB übernehmen (übernommen)");
    expect(memory).toBeChecked();
    expect(memory).toBeDisabled();
    expect(screen.getByText("1 Teil übernommen.")).toBeInTheDocument();

    failOn = new Set();
    fireEvent.click(screen.getByRole("button", { name: "1 Teil übernehmen" }));
    await waitFor(() => expect(screen.queryByTestId("scan-result")).toBeNull());
    expect(posts().map((b) => b.name)).toEqual(["AMD Ryzen 7 5700G", "Arbeitsspeicher 64 GB", "AMD Ryzen 7 5700G"]);
  });
});

function upload(content: string | Uint8Array<ArrayBuffer>, name = "bestellungen.csv") {
  const file = new File([content], name, { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("CSV-Datei wählen"), { target: { files: [file] } });
}

describe("Bestellliste einlesen", () => {
  const AMAZON = [
    '"Website","Order Date","Currency","Unit Price","Quantity","ASIN","Order Status","Product Name"',
    '"Amazon.de","2024-03-02T10:11:12Z","EUR","24.99","2","B07ABCDEF1","Closed","TP-Link Switch"',
    '"Amazon.de","2024-03-03T10:11:12Z","EUR","9.99","1","B07ABCDEF2","Closed","Alte Platte"',
    '"Amazon.de","2024-03-04T10:11:12Z","EUR","5.00","1","B07ABCDEF3","Closed","HDMI-Kabel"',
  ].join("\n");

  it("schon Vorhandenes (gleicher Name) ist nicht angekreuzt; Alle/Keine; übernommen wird mit Fortschritt", async () => {
    await openMenuEntry("Bestellliste einlesen (CSV)");
    upload(AMAZON);
    const result = await screen.findByTestId("import-result");
    expect(result.textContent).toContain("Amazon-Bestellverlauf „bestellungen.csv“, 3 Einträge");
    expect(within(result).getByLabelText("TP-Link Switch übernehmen")).toBeChecked();
    expect(within(result).getByLabelText("Alte Platte übernehmen (schon im Inventar?)")).not.toBeChecked();

    fireEvent.click(within(result).getByRole("button", { name: "Keine" }));
    expect(within(result).getByRole("button", { name: "0 Gegenstände übernehmen" })).toBeDisabled();
    fireEvent.click(within(result).getByRole("button", { name: "Alle ankreuzen" }));
    fireEvent.click(within(result).getByText("Alte Platte")); // bewusst wieder ab
    fireEvent.click(within(result).getByRole("button", { name: "2 Gegenstände übernehmen" }));

    expect(await screen.findByText("2 Gegenstände übernommen.")).toBeInTheDocument();
    expect(posts()).toEqual([
      expect.objectContaining({ name: "TP-Link Switch", quantity: 2, purchase_price_cents: 2499, product_url: "https://www.amazon.de/dp/B07ABCDEF1", category_id: null, location_id: null }),
      expect.objectContaining({ name: "HDMI-Kabel", purchase_price_cents: 500 }),
    ]);
    expect(posts()[0]).not.toHaveProperty("category");
    expect(screen.queryByTestId("import-result")).toBeNull();
  });

  it("gleiche Seriennummer oder gleicher Produktlink zählt auch als vorhanden; unbekannte Kategorien werden genannt", async () => {
    await openMenuEntry("Bestellliste einlesen (CSV)");
    upload("Name;Seriennummer;Produktlink;Kategorie;Standort\nPlatte neu;WX1;;Server;Keller\nSwitch neu;;https://example.com/switch;Netzwerk;Dachboden\nNeu;;;;\n");
    const result = await screen.findByTestId("import-result");
    expect(within(result).getByLabelText("Platte neu übernehmen (schon im Inventar?)")).not.toBeChecked();
    expect(within(result).getByLabelText("Switch neu übernehmen (schon im Inventar?)")).not.toBeChecked();
    expect(within(result).getByLabelText("Neu übernehmen")).toBeChecked();
    expect(result.textContent).toContain("Diese Kategorien gibt es hier nicht, die Gegenstände bleiben ohne Kategorie: Netzwerk.");
    expect(result.textContent).toContain("Diese Standorte gibt es hier nicht, es gilt der Standort unten: Dachboden.");
  });

  it("scheitern einzelne Zeilen: die anderen kommen rein, ein zweiter Versuch legt nichts doppelt an", async () => {
    failOn = new Set(["TP-Link Switch"]);
    await openMenuEntry("Bestellliste einlesen (CSV)");
    upload(AMAZON);
    fireEvent.click(await screen.findByRole("button", { name: "2 Gegenstände übernehmen" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("1 übernommen. 1 Gegenstand ging nicht.");
    expect(screen.getByLabelText("HDMI-Kabel übernehmen (übernommen)")).toBeDisabled();

    failOn = new Set();
    fireEvent.click(screen.getByRole("button", { name: "1 Gegenstand übernehmen" }));
    await waitFor(() => expect(screen.queryByTestId("import-result")).toBeNull());
    expect(posts().map((b) => b.name)).toEqual(["TP-Link Switch", "HDMI-Kabel", "TP-Link Switch"]);
  });

  it("eine Excel-Datei im Windows-Zeichensatz kommt mit Umlauten an; eine Datei ohne Namensspalte sagt, was fehlt", async () => {
    await openMenuEntry("Bestellliste einlesen (CSV)");
    upload(new Uint8Array([...new TextEncoder().encode("Name;Menge\n"), 0x47, 0x72, 0xf6, 0xdf, 0x65, 0x3b, 0x31, 0x0a]));
    expect(await screen.findByLabelText("Größe übernehmen")).toBeChecked();

    upload("Preis;Menge\n1;2\n", "falsch.csv");
    expect(await screen.findByText(/Die Datei braucht eine Spalte mit dem Namen/)).toBeInTheDocument();
  });

  it("nach fünf Fehlern in Folge ist Schluss, und es steht da, wie viele nicht versucht wurden", async () => {
    const names = Array.from({ length: 8 }, (_, i) => `Teil ${i}`);
    failOn = new Set(names);
    await openMenuEntry("Bestellliste einlesen (CSV)");
    upload(["Name", ...names].join("\n"));
    fireEvent.click(await screen.findByRole("button", { name: "8 Gegenstände übernehmen" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("5 Gegenstände gingen nicht – nach mehreren Fehlern in Folge abgebrochen, 3 nicht versucht.");
    expect(posts()).toHaveLength(5);
  });

  it("mit Suche gelten die Knöpfe nur für die gefundenen Zeilen und sagen das; ohne Gegenstandsliste nichts vorab", async () => {
    await openMenuEntry("Bestellliste einlesen (CSV)");
    itemsFail = true; // erst jetzt: die Seite selbst hat ihre Liste schon
    upload(["Name", ...Array.from({ length: 10 }, (_, i) => `Kabel ${i}`), "Switch"].join("\n"));
    const result = await screen.findByTestId("import-result");
    expect(result.textContent).toContain("Konnte nicht prüfen, was schon im Inventar steht.");
    expect(within(result).getByRole("button", { name: "0 Gegenstände übernehmen" })).toBeDisabled();
    fireEvent.change(within(result).getByLabelText("In der eingelesenen Liste suchen"), { target: { value: "kabel" } });
    fireEvent.click(within(result).getByRole("button", { name: "Alle gefundenen ankreuzen" }));
    expect(within(result).getByRole("button", { name: "10 Gegenstände übernehmen" })).toBeEnabled();
    fireEvent.click(within(result).getByRole("button", { name: "Gefundene abwählen" }));
    expect(within(result).getByRole("button", { name: "0 Gegenstände übernehmen" })).toBeDisabled();
  });
});
