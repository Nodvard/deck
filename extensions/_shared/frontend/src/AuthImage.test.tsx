import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthImage } from "./AuthImage";

const realCreate = URL.createObjectURL;
const realRevoke = URL.revokeObjectURL;

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
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:bild-${++n}`);
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  // Erst abbauen (AuthImage gibt dabei seine Blob-URL frei), dann die Stubs zuruecksetzen.
  cleanup();
  URL.createObjectURL = realCreate;
  URL.revokeObjectURL = realRevoke;
  vi.unstubAllGlobals();
});

describe("AuthImage", () => {
  it("laedt das Bild mit Login-Token und zeigt es als Blob-URL an", async () => {
    const fetchMock = vi.fn(async () => new Response("png", { status: 200, headers: { "Content-Type": "image/png" } }));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<AuthImage src="/api/v1/ext/inventory/items/i1/images/b1" alt="Foto" className="h-11 w-11" />);

    await waitFor(() => expect(container.querySelector("img")).toHaveAttribute("src", "blob:bild-1"));
    const img = container.querySelector("img");
    expect(img).toHaveAttribute("alt", "Foto");
    expect(img).toHaveClass("h-11", "w-11");
    // Die URL wird unveraendert benutzt (kein zweites /api/v1), der Token geht als Bearer mit.
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/ext/inventory/items/i1/images/b1");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("erneuert bei 401 das Token und lädt das Bild danach", async () => {
    let token = "alt";
    window.__lattice.getAccessToken = () => token;
    window.__lattice.refreshAccessToken = vi.fn(async () => (token = "neu"));
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) =>
      new Response("png", { status: new Headers(init.headers).get("Authorization") === "Bearer neu" ? 200 : 401 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<AuthImage src="/api/v1/ext/inventory/items/i1/images/b1" alt="Foto" className="h-11 w-11" />);

    await waitFor(() => expect(container.querySelector("img")).toHaveAttribute("src", "blob:bild-1"));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("gibt die Blob-URL beim Entfernen und beim Wechsel der Quelle wieder frei", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("png", { status: 200 })));

    const { container, rerender, unmount } = render(<AuthImage src="/api/v1/a" alt="Foto" />);
    await waitFor(() => expect(container.querySelector("img")).toHaveAttribute("src", "blob:bild-1"));

    rerender(<AuthImage src="/api/v1/b" alt="Foto" />);
    await waitFor(() => expect(container.querySelector("img")).toHaveAttribute("src", "blob:bild-2"));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:bild-1");

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:bild-2");
  });

  it("zeigt bei einem Fehler einen Platzhalter statt eines kaputten Bildes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 401 })));

    const { container } = render(<AuthImage src="/api/v1/x" alt="Foto" className="h-20 w-20" />);

    const placeholder = await screen.findByTitle("Foto konnte nicht geladen werden");
    expect(placeholder).toHaveClass("h-20", "w-20");
    expect(container.querySelector("img")).toBeNull();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
