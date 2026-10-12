import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentsPage } from "./DocumentsPage";

/** Dokumente mit Gegenständen im Inventar verknüpfen: Namen anzeigen, zuordnen, lösen, filtern. */

const ITEMS = [{ id: "item-nas", name: "NAS" }, { id: "item-switch", name: "Switch" }];
const DOC = {
  id: "doc-1", original_filename: "rechnung.pdf", content_type: "application/pdf", size_bytes: 40000, page_count: 1,
  ocr_text: "RECHNUNG", ocr_status: "done", ocr_error: null, tags: [], item_ids: ["item-nas", "item-weg"],
};

let calls: { method: string; url: string }[];
let permissions: Set<string>;

beforeEach(() => {
  calls = [];
  permissions = new Set(["documents.read", "documents.write", "inventory.read"]);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(),
    hasPermission: vi.fn((p: string) => permissions.has(p)),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET" && url.includes("/ext/documents/documents")) return json([DOC]);
    if (method === "GET" && url.endsWith("/ext/documents/tags")) return json([]);
    if (method === "GET" && url.endsWith("/ext/inventory/items")) return json(ITEMS);
    if (url.match(/\/documents\/doc-1\/items\/[^/]+$/) && (method === "POST" || method === "DELETE")) return new Response(null, { status: 204 });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
});

describe("Dokumente und Inventar", () => {
  it("zeigt die Gegenstände mit Namen; ein gelöschter heißt so und lässt sich lösen", async () => {
    render(<DocumentsPage />);
    const row = await screen.findByTestId("document-doc-1");
    await waitFor(() => expect(within(row).getByText("NAS")).toBeInTheDocument());
    expect(within(row).getByText("Gegenstand nicht mehr da")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Zuordnung zu NAS lösen" })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Zuordnung zu einem gelöschten Gegenstand lösen" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "DELETE", url: expect.stringContaining("/ext/documents/documents/doc-1/items/item-weg") }));
  });

  it("ordnet im Detailbereich einem Gegenstand zu; schon verknüpfte stehen nicht zur Wahl", async () => {
    render(<DocumentsPage />);
    const row = await screen.findByTestId("document-doc-1");
    fireEvent.click(within(row).getByRole("button", { name: "Details anzeigen" }));
    const select = await within(row).findByLabelText("rechnung.pdf einem Gegenstand zuordnen") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Gehört zu Gegenstand …", "Switch"]);
    fireEvent.change(select, { target: { value: "item-switch" } });
    fireEvent.click(within(row).getByRole("button", { name: "Zuordnen" }));
    await waitFor(() => expect(calls).toContainEqual({ method: "POST", url: expect.stringContaining("/ext/documents/documents/doc-1/items/item-switch") }));
  });

  it("filtert nach Gegenstand", async () => {
    render(<DocumentsPage />);
    fireEvent.change(await screen.findByLabelText("Nach Gegenstand filtern"), { target: { value: "item-nas" } });
    await waitFor(() => expect(calls.some((c) => c.method === "GET" && c.url.includes("item_id=item-nas"))).toBe(true));
  });

  it("ohne Recht am Inventar: keine Abfrage, keine Zuordnung, verknüpfte Dokumente heißen allgemein", async () => {
    permissions = new Set(["documents.read", "documents.write"]);
    render(<DocumentsPage />);
    const row = await screen.findByTestId("document-doc-1");
    fireEvent.click(within(row).getByRole("button", { name: "Details anzeigen" }));
    expect(within(row).getAllByText("Gegenstand im Inventar")).toHaveLength(2);
    expect(within(row).queryByLabelText("rechnung.pdf einem Gegenstand zuordnen")).toBeNull();
    expect(screen.queryByLabelText("Nach Gegenstand filtern")).toBeNull();
    expect(calls.some((c) => c.url.includes("/ext/inventory/"))).toBe(false);
  });
});
