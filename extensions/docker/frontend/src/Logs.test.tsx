import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LogPanel, logFileName, MAX_LINES } from "./Logs";

const ENTRY = { name: "nginx", host: "docker", host_id: "h1", container: "nginx" };
const LINES = [
  "2026-10-15T07:00:00Z started",
  "2026-10-15T07:00:01Z GET /health 200",
  "2026-10-15T07:00:02Z [WARN] Health (slow)",
];

function serve(text: string) {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    urls.push(String(input));
    return new Response(text);
  }));
  return urls;
}

const box = () => screen.getByTestId("log-panel").querySelector("pre")!;

beforeEach(() => {
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("LogPanel", () => {
  it("ruft einen abgeschlossenen Zeitraum nicht erneut ab", async () => {
    vi.useFakeTimers();
    const urls = serve("gespeicherte Ausgabe\n");
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      fireEvent.change(screen.getByLabelText("Zeitraum"), { target: { value: "24h" } });
      await act(async () => {});
      expect(urls).toHaveLength(2);
      expect(box()).toHaveTextContent("gespeicherte Ausgabe");
      expect(screen.getByText("beendet")).toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(urls).toHaveLength(2);
      expect(box()).toHaveTextContent("gespeicherte Ausgabe");
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("begrenzt neue Verbindungen nach Stromende und behält die Ausgabe bei leeren Antworten", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("letzte Ausgabe\n"))
      .mockImplementation(async () => new Response(""));
    vi.stubGlobal("fetch", fetch);
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      expect(screen.getByText("verbindet erneut")).toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(fetch).toHaveBeenCalledTimes(4);
      expect(screen.getByText("beendet")).toBeInTheDocument();
      expect(box()).toHaveTextContent("letzte Ausgabe");
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it.each([
    ["Netzwerkfehler", () => Promise.reject(new TypeError("Verbindung unterbrochen"))],
    ["Proxyfehler", () => Promise.resolve(new Response("Bad Gateway", { status: 502 }))],
    ["Docker-Fehler", () => Promise.resolve(Response.json({ detail: "Docker startet noch" }, { status: 502 }))],
    ["Serverfehler", () => Promise.resolve(Response.json({ detail: "Fehler beim Lesen" }, { status: 500 }))],
  ])("zeigt bei %s die Wiederholung an und beendet sie nach drei Versuchen", async (_label, reply) => {
    vi.useFakeTimers();
    const fetch = vi.fn(reply);
    vi.stubGlobal("fetch", fetch);
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      expect(screen.getByText("verbindet erneut")).toBeInTheDocument();
      expect(screen.queryByText("Fehler")).not.toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(fetch).toHaveBeenCalledTimes(4);
      expect(screen.getByText("Fehler")).toBeInTheDocument();
      expect(screen.queryByText("verbindet erneut")).not.toBeInTheDocument();
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("behält die Ausgabe, solange die neue Verbindung noch keine Zeilen liefert", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("letzte Ausgabe\n"))
      .mockResolvedValue(new Response(new ReadableStream()));
    vi.stubGlobal("fetch", fetch);
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(screen.getByText("live")).toBeInTheDocument();
      expect(box()).toHaveTextContent("letzte Ausgabe");
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("verbindet nach einem Berechtigungsfehler nicht erneut", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async () => Response.json({ detail: "Nicht erlaubt" }, { status: 403 }));
    vi.stubGlobal("fetch", fetch);
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(screen.getByText("Fehler")).toBeInTheDocument();
      expect(screen.getByText("Nicht erlaubt")).toBeInTheDocument();
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("bricht eine geplante Wiederholung beim Anhalten ab", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async () => new Response("letzte Ausgabe\n"));
    vi.stubGlobal("fetch", fetch);
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      fireEvent.click(screen.getByRole("button", { name: "Anhalten" }));
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(screen.getByText("angehalten")).toBeInTheDocument();
      expect(box()).toHaveTextContent("letzte Ausgabe");
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("verbindet nach einem Docker-Neustart bei Stromende und vorübergehenden Fehlern erneut", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response("vor dem Neustart\n"))
      .mockRejectedValueOnce(new TypeError("Verbindung unterbrochen"))
      .mockResolvedValueOnce(Response.json({ detail: "Docker startet noch" }, { status: 502 }))
      .mockResolvedValue(new Response(new ReadableStream({
        start(controller) { controller.enqueue(new TextEncoder().encode("nach dem Neustart\n")); },
      })));
    vi.stubGlobal("fetch", fetch);
    const view = render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    try {
      await act(async () => {});
      expect(box()).toHaveTextContent("vor dem Neustart");
      for (const attempts of [2, 3, 4]) {
        await act(() => vi.advanceTimersByTimeAsync(3000));
        expect(fetch).toHaveBeenCalledTimes(attempts);
      }
      expect(box()).toHaveTextContent("nach dem Neustart");
      expect(screen.getByText("live")).toBeInTheDocument();
      expect(screen.queryByText("Docker startet noch")).not.toBeInTheDocument();
      view.unmount();
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(fetch).toHaveBeenCalledTimes(4);
      expect(fetch.mock.calls[3][1].signal.aborted).toBe(true);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });

  it("Zeitraum: holt bis zu 5000 Zeilen seit dann und sagt, wenn es keine gibt", async () => {
    const urls = serve("");
    render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    await waitFor(() => expect(urls).toEqual(["/api/v1/ext/docker/containers/h1/nginx/logs?tail=200"]));

    fireEvent.change(screen.getByLabelText("Zeitraum"), { target: { value: "24h" } });
    await waitFor(() => expect(urls[1]).toBe(`/api/v1/ext/docker/containers/h1/nginx/logs?tail=${MAX_LINES}&since=24h`));
    expect(await screen.findByText("In diesem Zeitraum gibt es keine Ausgabe.")).toBeInTheDocument();
  });

  it("Suche: Groß- und Kleinschreibung egal, Treffer markiert und gezählt, Sonderzeichen sind normale Zeichen", async () => {
    serve(LINES.join("\n") + "\n");
    render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    await waitFor(() => expect(box().textContent).toContain("started"));

    fireEvent.change(screen.getByLabelText("In den Logs suchen"), { target: { value: "HEALTH" } });
    expect(box().textContent).toBe(`${LINES[1]}\n${LINES[2]}`);
    expect([...box().querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["health", "Health"]);
    expect(screen.getByTestId("log-hits").textContent).toBe("2 von 3 Zeilen");

    fireEvent.change(screen.getByLabelText("In den Logs suchen"), { target: { value: "(slow" } });
    expect(box().textContent).toBe(LINES[2]);

    fireEvent.change(screen.getByLabelText("In den Logs suchen"), { target: { value: "gibt es nicht" } });
    expect(box().textContent).toBe("Keine passenden Zeilen unter den geladenen.");
    expect(screen.getByRole("button", { name: "Treffer herunterladen" })).toBeDisabled();
  });

  it("Herunterladen speichert die Zeilen, bei einer Suche nur die Treffer", async () => {
    const saved: Blob[] = [];
    const names: string[] = [];
    const { createObjectURL, revokeObjectURL } = URL;
    URL.createObjectURL = (blob: Blob) => { saved.push(blob); return "blob:logs"; };
    URL.revokeObjectURL = () => {};
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    try {
      serve(LINES.join("\n") + "\n");
      render(<LogPanel entry={ENTRY} onClose={() => {}} />);
      await waitFor(() => expect(box().textContent).toContain("started"));

      fireEvent.click(screen.getByRole("button", { name: "Herunterladen" }));
      expect(await saved[0].text()).toBe(LINES.join("\n") + "\n");
      expect(names[0]).toMatch(/^nginx-logs-\d{4}-\d{2}-\d{2}-\d{4}\.txt$/);

      fireEvent.change(screen.getByLabelText("In den Logs suchen"), { target: { value: "warn" } });
      fireEvent.click(screen.getByRole("button", { name: "Treffer herunterladen" }));
      expect(await saved[1].text()).toBe(`${LINES[2]}\n`);
    } finally {
      Object.assign(URL, { createObjectURL, revokeObjectURL });
      click.mockRestore();
    }
  });

  it("mehr als 5000 Zeilen: die ältesten fallen weg, und das steht da", async () => {
    serve(Array.from({ length: MAX_LINES + 1 }, (_, i) => `Zeile ${i}`).join("\n") + "\n");
    render(<LogPanel entry={ENTRY} onClose={() => {}} />);
    expect(await screen.findByTestId("log-cut")).toBeInTheDocument();
    expect(box().textContent?.startsWith("Zeile 1\n")).toBe(true);
  });
});

describe("logFileName", () => {
  it("Container, Datum und Uhrzeit", () => {
    expect(logFileName("web.1", new Date(2026, 9, 5, 7, 3))).toBe("web.1-logs-2026-10-05-0703.txt");
  });
});
