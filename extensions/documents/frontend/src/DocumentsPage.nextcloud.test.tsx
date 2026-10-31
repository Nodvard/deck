import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentsPage, inboxSummary } from "./DocumentsPage";

/** Nextcloud-Ordner als Eingang: Abschnitt nur mit eingestelltem Ordner, Knopf „Jetzt abholen“, Ergebnis in Worten. */

const RUN = {
  at: "2026-10-09T08:15:00Z", ok: true, message: null, imported: 2, too_large: [], wrong_content: [], failed: [], more_waiting: false,
};

let calls: { method: string; url: string }[];
let inbox: Record<string, unknown>;
let docs: unknown[];

beforeEach(() => {
  calls = [];
  docs = [];
  inbox = { folder: "/Scans", folder_error: null, nextcloud_ready: true, running: false, last_run: null };
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(),
    hasPermission: vi.fn(() => true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (method === "GET" && url.includes("/ext/documents/documents")) return json(docs);
    if (method === "GET" && url.endsWith("/ext/documents/tags")) return json([]);
    if (method === "GET" && url.endsWith("/ext/inventory/items")) return json([]);
    if (method === "GET" && url.endsWith("/ext/documents/inbox")) return json(inbox);
    if (method === "POST" && url.endsWith("/ext/documents/inbox/fetch")) {
      // Läuft erst im Hintergrund; beim nächsten Nachsehen ist er fertig.
      const started = { ...inbox, running: true };
      inbox = { ...inbox, running: false, last_run: { ...RUN, imported: 1, too_large: ["riesig.pdf"] } };
      return json(started, 202);
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
});

describe("Nextcloud-Ordner", () => {
  it("fehlt ganz, solange kein Ordner eingestellt ist", async () => {
    inbox = { ...inbox, folder: null };
    render(<DocumentsPage />);
    await screen.findByText("Noch keine Dokumente erfasst.");
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/ext/documents/inbox"))).toBe(true));
    expect(screen.queryByText(/Nextcloud-Ordner/)).not.toBeInTheDocument();
  });

  it("holt auf Knopfdruck ab, wartet auf das Ende, sagt das Ergebnis und lädt die Liste neu", async () => {
    inbox = { ...inbox, last_run: RUN };
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<DocumentsPage />);
    expect(await screen.findByText(/Nextcloud-Ordner \/Scans/)).toBeInTheDocument();
    expect(screen.getByText(/2 neue Dokumente übernommen\./)).toBeInTheDocument();
    const before = calls.filter((c) => c.url.includes("/ext/documents/documents")).length;
    fireEvent.click(screen.getByRole("button", { name: /Jetzt abholen/ }));
    await vi.advanceTimersByTimeAsync(1600);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(
      "1 neues Dokument übernommen. Zu groß (über 20 MB), übersprungen: riesig.pdf.",
    ));
    expect(screen.getAllByText(/riesig\.pdf/)).toHaveLength(1);
    expect(calls).toContainEqual({ method: "POST", url: expect.stringContaining("/ext/documents/inbox/fetch") });
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/ext/documents/documents")).length).toBeGreaterThan(before));
    vi.useRealTimers();
  });

  it("wartet auch auf einen Abruf, der beim Öffnen schon läuft, und nennt die letzte Nachschau", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    inbox = { ...inbox, running: true, last_run: RUN };
    render(<DocumentsPage />);
    expect(await screen.findByRole("button", { name: /Holt ab/ })).toBeDisabled();
    inbox = { ...inbox, running: false, last_checked_at: "2026-10-09T09:00:00Z" };
    await vi.advanceTimersByTimeAsync(1600);
    expect(await screen.findByRole("button", { name: /Jetzt abholen/ })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent(/2 neue Dokumente übernommen\. Zuletzt nachgesehen um/);
    vi.useRealTimers();
  });

  it("startet bei Suche oder Filter keine zweite Warteschleife", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    docs = [{
      id: "doc-1", original_filename: "rechnung.pdf", content_type: "application/pdf", size_bytes: 40000, page_count: 1,
      ocr_text: "RECHNUNG", ocr_status: "done", ocr_error: null, tags: [], item_ids: [],
    }];
    inbox = { ...inbox, running: true, last_run: RUN };
    render(<DocumentsPage />);
    expect(await screen.findByRole("button", { name: /Holt ab/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Volltextsuche"), { target: { value: "rech" } });
    fireEvent.change(screen.getByLabelText("Volltextsuche"), { target: { value: "rechnung" } });
    const polls = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/ext/documents/inbox")).length;
    const before = polls();
    await vi.advanceTimersByTimeAsync(1600);
    expect(polls() - before).toBe(1);
    await vi.advanceTimersByTimeAsync(1500);
    expect(polls() - before).toBe(2);
    vi.useRealTimers();
  });

  it("ohne eingerichtete Nextcloud: Hinweis statt Knopf", async () => {
    inbox = { ...inbox, nextcloud_ready: false };
    render(<DocumentsPage />);
    expect(await screen.findByText(/Nextcloud ist noch nicht eingerichtet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Jetzt abholen/ })).not.toBeInTheDocument();
  });

  it("fasst ein Ergebnis in Worten zusammen", () => {
    expect(inboxSummary({ ...RUN, imported: 0 })).toBe("Keine neuen Dateien.");
    expect(inboxSummary({ ...RUN, ok: false, message: "Der Ordner /Scans ließ sich nicht lesen." })).toBe("Der Ordner /Scans ließ sich nicht lesen.");
    expect(inboxSummary({ ...RUN, imported: 1, wrong_content: ["a.pdf"], failed: ["b.png"], more_waiting: true })).toBe(
      "1 neues Dokument übernommen. Inhalt passt nicht zur Endung, übersprungen: a.pdf. "
      + "Nicht lesbar, beim nächsten Abruf neuer Versuch: b.png. Weitere Dateien folgen beim nächsten Abruf.",
    );
  });
});
