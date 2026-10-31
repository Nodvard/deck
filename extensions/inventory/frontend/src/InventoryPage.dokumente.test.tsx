import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InventoryPage } from "./InventoryPage";

/** Dokumente am Gegenstand: Liste, hochladen mit Zuordnung, lösen; ohne Modul oder Recht kein Bereich. */

const ITEMS = [{ id: "item-1", name: "NAS", description: null, category_id: null, location_id: null, quantity: 1, purchase_date: null, purchase_price_cents: null, warranty_until: null, notes: null, images: [] }];
const LINKED = [{ id: "doc-1", original_filename: "rechnung.pdf", content_type: "application/pdf" }];

let calls: { method: string; url: string }[];
let permissions: Set<string>;
let documentsStatus: number;

beforeEach(() => {
  calls = [];
  documentsStatus = 200;
  permissions = new Set(["inventory.read", "inventory.write", "documents.read", "documents.write"]);
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(),
    hasPermission: vi.fn((p: string) => permissions.has(p)),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET" && url.includes("/ext/documents/documents?item_id=")) {
      return documentsStatus === 200 ? json(LINKED) : json({ detail: documentsStatus === 404 ? "Nicht gefunden" : "Datenbank gesperrt" }, documentsStatus);
    }
    if (method === "POST" && url.includes("/ext/documents/documents?filename=bild.gif")) {
      return json({ detail: "Nicht unterstützter Dokumenttyp: image/gif" }, 415);
    }
    if (method === "POST" && url.includes("/ext/documents/documents?filename=")) return json({ id: "doc-2" }, 201);
    if (method === "DELETE" && url.includes("/ext/documents/documents/doc-1/items/item-1")) return new Response(null, { status: 204 });
    if (method === "GET" && url.includes("/ext/inventory/items")) return json(ITEMS);
    if (method === "GET" && /\/ext\/inventory\/(categories|locations|projects)$/.test(url)) return json([]);
    if (method === "GET" && url.endsWith("/hosts")) return json([]);
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
});

async function openItem() {
  render(<InventoryPage />);
  await screen.findByText("NAS");
  fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));
}

describe("Dokumente am Gegenstand", () => {
  it("listet die zugeordneten Dokumente; hochladen ordnet gleich zu; lösen trennt nur die Zuordnung", async () => {
    await openItem();
    const section = await screen.findByTestId("documents-item-1");
    expect(await within(section).findByText("rechnung.pdf")).toBeInTheDocument();

    const file = new File(["pdf"], "handbuch.pdf", { type: "application/pdf" });
    fireEvent.change(within(section).getByLabelText("Dokument zu NAS hochladen"), { target: { files: [file] } });
    await waitFor(() => expect(calls).toContainEqual({
      method: "POST", url: expect.stringMatching(/\/ext\/documents\/documents\?filename=handbuch\.pdf&item_id=item-1$/),
    }));

    fireEvent.click(within(section).getByRole("button", { name: "Zuordnung von rechnung.pdf zu diesem Gegenstand lösen" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "DELETE", url: expect.stringContaining("/ext/documents/documents/doc-1/items/item-1") }));
  });

  it("ist das Modul Dokumente aus, gibt es den Bereich nicht", async () => {
    documentsStatus = 404;
    await openItem();
    await waitFor(() => expect(calls.some((c) => c.url.includes("/ext/documents/"))).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("documents-item-1")).toBeNull());
  });

  it("ohne Recht an den Dokumenten: keine Abfrage, kein Bereich", async () => {
    permissions.delete("documents.read");
    permissions.delete("documents.write");
    await openItem();
    expect(screen.queryByTestId("documents-item-1")).toBeNull();
    expect(calls.some((c) => c.url.includes("/ext/documents/"))).toBe(false);
  });

  it("nur lesen: Liste ja, Hochladen und Lösen nein", async () => {
    permissions.delete("documents.write");
    await openItem();
    const section = await screen.findByTestId("documents-item-1");
    expect(await within(section).findByText("rechnung.pdf")).toBeInTheDocument();
    expect(within(section).queryByLabelText("Dokument zu NAS hochladen")).toBeNull();
    expect(within(section).queryByRole("button", { name: /lösen/ })).toBeNull();
  });

  it("ein Ladefehler steht mit Grund da", async () => {
    documentsStatus = 500;
    await openItem();
    const section = await screen.findByTestId("documents-item-1");
    expect(await within(section).findByText(/Die Liste der Dokumente ließ sich gerade nicht laden: Datenbank gesperrt/)).toBeInTheDocument();
  });

  it("ein falscher Dateityp beim Hochladen steht als Fehler da, nicht als Ladefehler der Liste", async () => {
    await openItem();
    const section = await screen.findByTestId("documents-item-1");
    await within(section).findByText("rechnung.pdf");
    const file = new File(["gif"], "bild.gif", { type: "image/gif" });
    fireEvent.change(within(section).getByLabelText("Dokument zu NAS hochladen"), { target: { files: [file] } });
    expect(await within(section).findByText("Fehler: Nicht unterstützter Dokumenttyp: image/gif")).toBeInTheDocument();
    expect(within(section).queryByText(/ließ sich gerade nicht laden/)).toBeNull();
    expect(within(section).getByText("rechnung.pdf")).toBeInTheDocument();
  });
});
