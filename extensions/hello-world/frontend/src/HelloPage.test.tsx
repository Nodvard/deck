import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SERVER_UNAVAILABLE_TEXT } from "../../../_shared/frontend/src/api";

import { HelloPage } from "./HelloPage";

const WIDGET = { data: [{ title: "Hallo Welt", subtitle: "7 Ticks seit Start" }], meta: {} };

let token: string | null;
let refresh: ReturnType<typeof vi.fn>;

beforeEach(() => {
  token = "alt";
  refresh = vi.fn(async (): Promise<NodvardDeckTokenRefreshResult> => {
    token = "neu";
    return { status: "ok", token };
  });
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => token,
    refreshAccessTokenResult: refresh as unknown as () => Promise<NodvardDeckTokenRefreshResult>,
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Partial<Window>).__nodvardDeck;
});

function bearer(call: unknown[]): string | null {
  return new Headers((call[1] as RequestInit).headers).get("Authorization");
}

describe("HelloPage", () => {
  it("zeigt die Daten des Widgets, nachdem sie geladen sind", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(WIDGET), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<HelloPage />);

    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(await screen.findByText("7 Ticks seit Start")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
    const call = (fetchMock.mock.calls as unknown as unknown[][])[0];
    expect(call[0]).toBe("/api/v1/ext/hello-world/widgets/hello");
    expect(bearer(call)).toBe("Bearer alt");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("erneuert bei abgelaufener Anmeldung (401) das Token und zeigt dann die Daten", async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) =>
      new Headers(init.headers).get("Authorization") === "Bearer neu"
        ? new Response(JSON.stringify(WIDGET), { status: 200 })
        : new Response(JSON.stringify({ detail: "Nicht authentifiziert." }), { status: 401 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<HelloPage />);

    expect(await screen.findByText("7 Ticks seit Start")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("zeigt den Fehlertext des Servers statt eines allgemeinen Satzes", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Keine Berechtigung." }), { status: 403 })));

    render(<HelloPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Keine Berechtigung.");
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("sagt, dass der Server gerade nicht erreichbar ist, wenn gar keine Antwort kommt, und lädt auf Wunsch neu", async () => {
    let up = false;
    const fetchMock = vi.fn(async () => {
      if (!up) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify(WIDGET), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<HelloPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(SERVER_UNAVAILABLE_TEXT);

    up = true;
    fireEvent.click(screen.getByRole("button", { name: "Neu laden" }));

    expect(await screen.findByText("7 Ticks seit Start")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("bricht den Aufruf ab, wenn die Seite verlassen wird, und zeigt danach keinen Fehler mehr", async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => {
        signal = init.signal as AbortSignal;
        return new Promise<Response>((_resolve, reject) => {
          signal?.addEventListener("abort", () => reject(new DOMException("abgebrochen", "AbortError")));
        });
      }),
    );
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const { unmount } = render(<HelloPage />);
    await waitFor(() => expect(signal).toBeDefined());
    expect(signal?.aborted).toBe(false);

    unmount();

    expect(signal?.aborted).toBe(true);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });
});
