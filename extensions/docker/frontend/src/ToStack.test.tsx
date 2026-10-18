import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ToStackPanel } from "./ToStack";

const PLAN = {
  container: "plaintest", id: "c".repeat(64), project: "plaintest", folder: "/opt/stacks/plaintest",
  compose: 'services:\n  plaintest:\n    image: "busybox:1.37"\n', env_names: ["FOO", "TZ"], refused: [], warnings: [],
};
const bodies: unknown[] = [];
const requests: string[] = [];

function serve(over: Record<string, (init?: RequestInit) => Response> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url}`);
    if (typeof init?.body === "string") bodies.push(init.body);
    for (const [part, answer] of Object.entries(over)) {
      if (url.includes(part)) return answer(init);
    }
    if (url.endsWith("/to-stack")) {
      return init?.method === "POST"
        ? Response.json({ action_id: "t1", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json(PLAN);
    }
    throw new Error(url);
  }));
}

const confirm = () => window.__lattice.confirmDialog as Mock;

beforeEach(() => {
  bodies.length = 0;
  requests.length = 0;
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("In Stack umwandeln", () => {
  it("zeigt die Datei ohne Werte und wandelt über die Freigabe um", async () => {
    const onDone = vi.fn();
    serve({
      "/actions/t1/approve": () => Response.json({ id: "t1", status: "succeeded", result: {
        output: "„plaintest“ läuft jetzt als Stack „plaintest“ aus /opt/stacks/plaintest. Container läuft stabil." } }),
    });
    render(<ToStackPanel hostId="h1" container="plaintest" onDone={onDone} />);
    const panel = await screen.findByTestId("to-stack-plaintest");
    expect(screen.getByLabelText("Compose-Datei des neuen Stacks").textContent).toBe(PLAN.compose);
    expect(panel.textContent).toContain("Die Umgebungsvariablen (FOO, TZ) kommen in die Datei container.env");
    fireEvent.click(screen.getByRole("button", { name: "Umwandeln" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("„plaintest“ läuft jetzt als Stack „plaintest“ aus /opt/stacks/plaintest. Container läuft stabil."));
    expect(String(confirm().mock.calls[0][0])).toMatch(/^„plaintest“ in einen Stack in \/opt\/stacks\/plaintest umwandeln\? /);
    expect(String(confirm().mock.calls[0][0])).toContain("Klappt das, löscht Deck den alten Container; was nur in ihm lag");
    expect(panel.textContent).toContain("Den alten Container löscht Deck, sobald der neue läuft");
    expect(panel.textContent).not.toContain("in der Freigabe");
    expect(requests).toContain("POST /api/v1/ext/docker/hosts/h1/containers/plaintest/to-stack");
  });

  it("nennt Gründe dagegen und sperrt den Knopf", async () => {
    serve({ "/to-stack": () => Response.json({ ...PLAN, refused: ["Der Container nutzt alte Links (--link)."], warnings: ["„x“ bekommt alle Rechte des Servers (privileged)."] }) });
    render(<ToStackPanel hostId="h1" container="plaintest" onDone={vi.fn()} />);
    const panel = await screen.findByTestId("to-stack-plaintest");
    expect(screen.getByRole("alert").textContent).toBe("Der Container nutzt alte Links (--link).");
    expect(panel.textContent).toContain("Riskant: „x“ bekommt alle Rechte des Servers (privileged).");
    expect(screen.getByRole("button", { name: "Umwandeln" })).toBeDisabled();
  });

  it("ein gescheitertes Umwandeln kommt als Warnung zurück", async () => {
    const onDone = vi.fn();
    serve({ "/actions/t1/approve": () => Response.json({ id: "t1", status: "failed", result: {
      error: "Der Stack ist nicht sauber angelaufen: Container startet immer wieder neu. Der alte Container läuft wieder wie vorher." } }) });
    render(<ToStackPanel hostId="h1" container="plaintest" onDone={onDone} />);
    fireEvent.click(await screen.findByRole("button", { name: "Umwandeln" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(
      "Der Stack ist nicht sauber angelaufen: Container startet immer wieder neu. Der alte Container läuft wieder wie vorher.", true));
  });

  it("ein Ladefehler sagt es und bietet einen neuen Versuch", async () => {
    serve({ "/to-stack": () => new Response("weg", { status: 502 }) });
    render(<ToStackPanel hostId="h1" container="plaintest" onDone={vi.fn()} />);
    expect(await screen.findByText(/Die Vorschau/)).toBeInTheDocument();
  });
});
