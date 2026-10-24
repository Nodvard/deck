import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentsPage } from "./DocumentsPage";

const TAGS = [{ id: "tag-1", name: "Rechnungen", match_keyword: "rechnung" }];
const DOCUMENTS = [
  {
    id: "doc-1", original_filename: "stromrechnung.pdf", content_type: "application/pdf", size_bytes: 40000,
    page_count: 2, ocr_text: "STROMANBIETER GMBH RECHNUNG", ocr_status: "done", ocr_error: null,
    tags: [{ id: "tag-1", name: "Rechnungen", match_keyword: "rechnung" }],
  },
];

function mockFetch(overrides: { documents?: unknown[]; onUpload?: (filename: string) => void; onRename?: (name: string) => void } = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.includes("/ext/documents/documents") && method === "GET") {
      return new Response(JSON.stringify(overrides.documents ?? DOCUMENTS), { status: 200 });
    }
    if (url.endsWith("/ext/documents/tags") && method === "GET") {
      return new Response(JSON.stringify(TAGS), { status: 200 });
    }
    if (url.endsWith("/ext/documents/tags") && method === "POST") {
      const body = JSON.parse(init?.body as string);
      return new Response(JSON.stringify({ id: "tag-2", match_keyword: null, ...body }), { status: 201 });
    }
    if (url.includes("/ext/documents/documents?filename=") && method === "POST") {
      const filename = new URL(url, "http://localhost").searchParams.get("filename") ?? "";
      overrides.onUpload?.(filename);
      return new Response(
        JSON.stringify({
          id: "doc-2", original_filename: filename, content_type: "image/png", size_bytes: 10,
          page_count: null, ocr_text: "NEU", ocr_status: "done", ocr_error: null, tags: [],
        }),
        { status: 201 },
      );
    }
    if (url.match(/\/documents\/[^/]+\/download$/) && method === "GET") {
      return new Response("dateiinhalt", { status: 200, headers: { "content-type": "application/pdf" } });
    }
    if (url.match(/\/documents\/[^/]+$/) && method === "PATCH") {
      overrides.onRename?.(JSON.parse(init?.body as string).original_filename);
      return new Response(JSON.stringify({ ...DOCUMENTS[0], original_filename: "neu.pdf" }), { status: 200 });
    }
    if (url.match(/\/documents\/[^/]+$/) && method === "DELETE") {
      return new Response(null, { status: 204 });
    }
    if (url.match(/\/documents\/[^/]+\/tags\/[^/]+$/) && (method === "POST" || method === "DELETE")) {
      return new Response(null, { status: 204 });
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

describe("DocumentsPage", () => {
  it("zeigt geladene Dokumente mit Typ, Seitenzahl, Größe und Tags", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<DocumentsPage />);

    await screen.findByText("stromrechnung.pdf");
    const row = within(screen.getByTestId("document-doc-1"));
    expect(row.getByText(/PDF · 2 Seite\(n\) · 39\.1 KB/)).toBeInTheDocument();
    expect(row.getByText("Rechnungen")).toBeInTheDocument();
  });

  it("laedt ein neues Dokument per Datei-Auswahl hoch", async () => {
    let uploadedFilename: string | null = null;
    vi.stubGlobal("fetch", mockFetch({ onUpload: (f) => (uploadedFilename = f) }));
    render(<DocumentsPage />);

    await screen.findByText("stromrechnung.pdf");
    const file = new File(["inhalt"], "neu.png", { type: "image/png" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(uploadedFilename).toBe("neu.png"));
  });

  it("fragt vor dem Loeschen eines Dokuments ueber den echten Dialog nach", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsPage />);

    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));

    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("stromrechnung.pdf"), expect.anything()));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/documents/doc-1"), expect.objectContaining({ method: "DELETE" })));
  });

  it("legt einen neuen Tag mit Auto-Stichwort ueber das Tag-Panel an", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsPage />);

    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: /Tags verwalten/ }));
    fireEvent.change(screen.getByLabelText("Neuer Tag"), { target: { value: "Vertraege" } });
    fireEvent.change(screen.getByLabelText("Auto-Stichwort"), { target: { value: "vertrag" } });
    fireEvent.click(screen.getByRole("button", { name: /Tag anlegen/ }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/ext/documents/tags"),
        expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "Vertraege", match_keyword: "vertrag" }) }),
      ),
    );
  });

  it("entfernt einen Tag von einem Dokument", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsPage />);

    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Tag Rechnungen entfernen" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/documents/doc-1/tags/tag-1"), expect.objectContaining({ method: "DELETE" })),
    );
  });

  it("laedt ein Dokument als Blob herunter statt per direktem Link (Auth-Header noetig)", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn().mockReturnValue("blob:mock");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Herunterladen" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/documents/doc-1/download"));
      expect(call).toBeDefined();
      expect(new Headers((call![1] as RequestInit).headers).get("Authorization")).toBe("Bearer tok");
    });
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
  });

  it("filtert die Suche ueber den q-Parameter", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<DocumentsPage />);

    await screen.findByText("stromrechnung.pdf");
    fireEvent.change(screen.getByLabelText("Volltextsuche"), { target: { value: "STROMANBIETER" } });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("q=STROMANBIETER"), expect.anything()));
  });

  it("zeigt einen Hinweis, wenn keine Dokumente erfasst sind", async () => {
    vi.stubGlobal("fetch", mockFetch({ documents: [] }));
    render(<DocumentsPage />);

    await screen.findByText(/Noch keine Dokumente erfasst/);
  });

  it("zeigt eine PDF-Vorschau im Browser und schließt sie wieder", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const created: string[] = [];
    URL.createObjectURL = vi.fn(() => { created.push("blob:x"); return "blob:x"; });
    URL.revokeObjectURL = vi.fn();
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Ansehen" }));
    const preview = await screen.findByTestId("preview-doc-1");
    expect(preview.querySelector("iframe")?.getAttribute("src")).toBe("blob:x");
    fireEvent.click(screen.getByRole("button", { name: "Vorschau schließen" }));
    await waitFor(() => expect(screen.queryByTestId("preview-doc-1")).toBeNull());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
  });

  it("gibt die Blob-URL der Vorschau frei, wenn die Seite verlassen wird", async () => {
    vi.stubGlobal("fetch", mockFetch());
    URL.createObjectURL = vi.fn(() => "blob:offen");
    URL.revokeObjectURL = vi.fn();
    const { unmount } = render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Ansehen" }));
    await screen.findByTestId("preview-doc-1");
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:offen");
  });

  it("die Vorschau nimmt den Typ aus der festen Liste, nicht den vom Server", async () => {
    // Ein Server (oder eine veränderte Sicherung) liefert HTML: Die Blob-URL bleibt trotzdem ein PDF.
    const html = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (
      String(input).endsWith("/download")
        ? new Response("<script>alert(1)</script>", { status: 200, headers: { "content-type": "text/html" } })
        : html(input, init)
    )));
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => { blobs.push(b); return "blob:x"; });
    URL.revokeObjectURL = vi.fn();
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Ansehen" }));
    await screen.findByTestId("preview-doc-1");
    expect(blobs.map((b) => b.type)).toEqual(["application/pdf"]);
  });

  it.each(["text/html", "image/svg+xml", "constructor", "application/xhtml+xml"])(
    "ohne feste Vorschau (%s) gibt es nur Herunterladen", async (contentType) => {
      vi.stubGlobal("fetch", mockFetch({ documents: [{ ...DOCUMENTS[0], content_type: contentType }] }));
      render(<DocumentsPage />);
      await screen.findByText("stromrechnung.pdf");
      expect(screen.queryByRole("button", { name: "Ansehen" })).toBeNull();
      expect(screen.getByRole("button", { name: /Herunterladen/ })).toBeInTheDocument();
      expect(document.querySelector("iframe")).toBeNull();
    },
  );

  it("benennt ein Dokument über die Rückfrage um", async () => {
    let renamed = "";
    vi.stubGlobal("fetch", mockFetch({ onRename: (n) => (renamed = n) }));
    window.__lattice.promptDialog = vi.fn().mockResolvedValue("  Strom 2026.pdf ");
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Umbenennen" }));
    await waitFor(() => expect(renamed).toBe("Strom 2026.pdf"));
  });
});

describe("DocumentsPage: Ladefehler und Fehlergründe", () => {
  /** Wie `mockFetch`, nur antwortet `match` mit `status` und dem Grund „Datenbank gesperrt“. */
  function failing(match: (url: string, method: string) => boolean, status = 500) {
    const state = { fail: true };
    const ok = mockFetch();
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (state.fail && match(String(input), init?.method ?? "GET")) return new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status });
      return ok(input, init);
    });
    vi.stubGlobal("fetch", fn);
    return state;
  }

  it("die Tags kommen nicht an: der Grund und „Erneut versuchen“ statt einer kaputten Seite", async () => {
    const state = failing((url) => url.endsWith("/ext/documents/tags"));
    render(<DocumentsPage />);
    const error = await screen.findByTestId("load-error");
    expect(error.textContent).toContain("Die Liste der Dokumente ließ sich gerade nicht laden: Datenbank gesperrt");
    state.fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("stromrechnung.pdf")).toBeInTheDocument();
  });

  it("Herunterladen scheitert: der Grund vom Server statt nur „HTTP 404“", async () => {
    failing((url) => url.endsWith("/download"), 404);
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: /Herunterladen/ }));
    expect(await screen.findByText("Fehler: Datenbank gesperrt")).toBeInTheDocument();
  });

  it("Vorschau scheitert: der Grund vom Server statt nur „HTTP 404“", async () => {
    failing((url) => url.endsWith("/download"), 404);
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Ansehen" }));
    expect(await screen.findByText("Fehler: Datenbank gesperrt")).toBeInTheDocument();
    expect(screen.queryByTestId("preview-doc-1")).toBeNull();
  });

  it("Löschen scheitert: der Grund steht da, das Dokument bleibt", async () => {
    failing((url, method) => url.endsWith("/documents/doc-1") && method === "DELETE");
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));
    expect(await screen.findByText("Fehler: Datenbank gesperrt")).toBeInTheDocument();
    expect(screen.getByText("stromrechnung.pdf")).toBeInTheDocument();
  });

  it("scheitert erst die Suche, bleibt die Seite mit Suchfeld stehen und der Grund steht darüber", async () => {
    failing((url, method) => url.includes("q=STROM") && method === "GET");
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.change(screen.getByLabelText("Volltextsuche"), { target: { value: "STROM" } });
    const error = await screen.findByTestId("load-error");
    expect(error.textContent).toContain("Die Liste der Dokumente ließ sich gerade nicht laden: Datenbank gesperrt");
    expect(error.textContent).toContain("letzte geladene Stand");
    expect(screen.getByLabelText("Volltextsuche")).toBeInTheDocument();
    expect(screen.getByText("stromrechnung.pdf")).toBeInTheDocument();
  });

  it("Tag entfernen scheitert: das steht da, statt still nichts zu tun", async () => {
    failing((url, method) => url.includes("/tags/tag-1") && method === "DELETE");
    render(<DocumentsPage />);
    await screen.findByText("stromrechnung.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Tag Rechnungen entfernen" }));
    expect(await screen.findByText("Fehler: Datenbank gesperrt")).toBeInTheDocument();
  });
});
