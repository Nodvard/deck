import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { parseLimit, TempLimit } from "./TempLimit";

interface Call { url: string; method: string; body: unknown }

const LIMIT_URL = "/api/v1/ext/system/hosts/h-1/temp-limit";

/** Ein Server mit der eigenen Grenze `own` und der allgemeinen 75 °C; `put` antwortet auf das Speichern. */
function mockLimit(calls: Call[], own: number | null, put?: (body: { own_c: number | null }) => Response) {
  let current = own;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url: String(input), method, body });
    if (String(input) !== LIMIT_URL) throw new Error(`Unerwarteter Fetch: ${input}`);
    if (method === "PUT") {
      if (put) return put(body);
      current = body.own_c;
    }
    return new Response(JSON.stringify({ host_id: "h-1", own_c: current, global_c: 75, effective_c: current ?? 75 }), { status: 200 });
  });
}

function permit(...granted: string[]) {
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: (p: string) => granted.includes(p),
  };
}

const field = () => screen.getByLabelText("Warnen ab (°C)") as HTMLInputElement;
const saveButton = () => screen.getByRole("button", { name: "Speichern" }) as HTMLButtonElement;

beforeEach(() => permit("settings.write"));

describe("TempLimit", () => {
  it("zeigt beim Laden einen Hinweis, dann die geltende Grenze", async () => {
    vi.stubGlobal("fetch", mockLimit([], null));
    render(<TempLimit hostId="h-1" />);
    expect(screen.getByText(/Lade die Temperaturgrenze/)).toBeInTheDocument();
    expect((await screen.findByTestId("temp-limit-current")).textContent).toBe("Dieser Server warnt ab 75 °C (die Grenze für alle Server).");
    expect(field().value).toBe("");
    expect(field().placeholder).toBe("75");
    expect(screen.queryByRole("button", { name: "Allgemeine Grenze nutzen" })).toBeNull();
  });

  it("nennt eine eigene Grenze und die allgemeine daneben", async () => {
    vi.stubGlobal("fetch", mockLimit([], 90));
    render(<TempLimit hostId="h-1" />);
    expect((await screen.findByTestId("temp-limit-current")).textContent).toBe(
      "Dieser Server warnt ab 90 °C (eigene Grenze). Für die anderen Server gilt 75 °C.",
    );
    expect(field().value).toBe("90");
  });

  it("ohne Recht nur die Anzeige, keine Eingabe und keine Knöpfe", async () => {
    permit("hosts.read");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockLimit(calls, 90));
    render(<TempLimit hostId="h-1" />);
    expect((await screen.findByTestId("temp-limit-current")).textContent).toContain("90 °C (eigene Grenze)");
    expect(screen.queryByLabelText("Warnen ab (°C)")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/Ändern dürfen nur Benutzer/)).toBeInTheDocument();
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
  });

  it("Speichern schickt die eingetippte Zahl und zeigt die neue Grenze", async () => {
    const calls: Call[] = [];
    const changed = vi.fn();
    vi.stubGlobal("fetch", mockLimit(calls, null));
    render(<TempLimit hostId="h-1" onChanged={changed} />);
    await screen.findByTestId("temp-limit");
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(field(), { target: { value: "90" } });
    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    expect((await screen.findByRole("status")).textContent).toBe("Gespeichert. Dieser Server warnt jetzt ab 90 °C.");
    const put = calls.find((c) => c.method === "PUT");
    expect(put).toEqual({ url: LIMIT_URL, method: "PUT", body: { own_c: 90 } });
    expect(screen.getByTestId("temp-limit-current").textContent).toContain("90 °C (eigene Grenze)");
    expect(screen.getByRole("button", { name: "Allgemeine Grenze nutzen" })).toBeInTheDocument();
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("„Allgemeine Grenze nutzen“ schickt null", async () => {
    const calls: Call[] = [];
    const changed = vi.fn();
    vi.stubGlobal("fetch", mockLimit(calls, 90));
    render(<TempLimit hostId="h-1" onChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "Allgemeine Grenze nutzen" }));
    expect((await screen.findByRole("status")).textContent).toBe("Gespeichert. Dieser Server warnt wieder ab 75 °C, wie alle anderen.");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ own_c: null });
    expect(screen.getByTestId("temp-limit-current").textContent).toContain("(die Grenze für alle Server)");
    expect(field().value).toBe("");
    expect(screen.queryByRole("button", { name: "Allgemeine Grenze nutzen" })).toBeNull();
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it.each(["39", "111", "90.5", "7"])("%s wird nicht geschickt, der Grund steht dabei", async (typed) => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockLimit(calls, null));
    render(<TempLimit hostId="h-1" />);
    await screen.findByTestId("temp-limit");
    fireEvent.change(field(), { target: { value: typed } });
    expect(saveButton().disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toBe("Bitte eine ganze Zahl von 40 bis 110 eingeben.");
    fireEvent.click(saveButton());
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("ein Fehler beim Speichern steht da, die Anzeige bleibt wie sie war", async () => {
    const changed = vi.fn();
    vi.stubGlobal("fetch", mockLimit([], null, () => new Response(JSON.stringify({ detail: "Dafür fehlt dir das Recht." }), { status: 403 })));
    render(<TempLimit hostId="h-1" onChanged={changed} />);
    await screen.findByTestId("temp-limit");
    fireEvent.change(field(), { target: { value: "90" } });
    fireEvent.click(saveButton());
    expect((await screen.findByRole("alert")).textContent).toBe("Dafür fehlt dir das Recht.");
    expect(screen.getByTestId("temp-limit-current").textContent).toContain("75 °C (die Grenze für alle Server)");
    expect(field().value).toBe("90");
    expect(saveButton().disabled).toBe(false);
    expect(changed).not.toHaveBeenCalled();
  });

  it("während des Speicherns ist alles gesperrt", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const fetchMock = mockLimit([], null);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") await gate;
      return fetchMock(input, init);
    }));
    render(<TempLimit hostId="h-1" />);
    await screen.findByTestId("temp-limit");
    fireEvent.change(field(), { target: { value: "90" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByRole("button", { name: "Speichert …" })).toBeDisabled());
    expect(field().disabled).toBe(true);
    release();
    await screen.findByRole("status");
    expect(field().disabled).toBe(false);
  });

  it("ein Ladefehler ist keine leere Anzeige, „Erneut versuchen“ lädt neu", async () => {
    const calls: Call[] = [];
    const ok = mockLimit(calls, 90);
    let failing = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (
      failing ? new Response(JSON.stringify({ detail: "Nicht erreichbar: timeout" }), { status: 502 }) : ok(input, init)
    )));
    render(<TempLimit hostId="h-1" />);
    expect((await screen.findByTestId("load-error")).textContent).toContain("Die Temperaturgrenze ließ sich gerade nicht laden: Nicht erreichbar: timeout");
    expect(screen.queryByLabelText("Warnen ab (°C)")).toBeNull();
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect((await screen.findByTestId("temp-limit-current")).textContent).toContain("90 °C (eigene Grenze)");
  });

  it("parseLimit nimmt nur ganze Zahlen von 40 bis 110", () => {
    expect([" 40", "110", "75"].map(parseLimit)).toEqual([40, 110, 75]);
    expect(["", "39", "111", "90.5", "-50", "1e2", "abc", "9999"].map(parseLimit)).toEqual([null, null, null, null, null, null, null, null]);
  });
});
