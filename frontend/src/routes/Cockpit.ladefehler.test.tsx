import { QueryClient, QueryClientProvider, onlineManager } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { previewState, respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { Cockpit } from "./Cockpit";

vi.mock("../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

/** Bisher: Kam die Serverliste nicht an, stand oben „Alles läuft rund. 0 von 0 Servern online“ und
 * rechts „Nichts offen – genieß die Ruhe“. Eine gescheiterte Abfrage ist keine Entwarnung. */

const CALM = { ...(respond("/api/v1/overview", "GET") as object), pending_actions: 0, attention: [] };

/** Liefert die Vorschau-Daten; Pfade in `failing` antworten mit 500 und einem Grund. */
function fetchWith(failing: Set<string>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = (typeof input === "string" ? input : input.toString()).replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    if (failing.has(key)) return new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status: 500 });
    const body = key === "/overview" ? CALM : respond(path, init?.method ?? "GET");
    if (body === undefined) throw new Error(`Unerwarteter Fetch: ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

function renderCockpit() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Cockpit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => onlineManager.setOnline(true));

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico Benks", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
  previewState.noHosts = false;
  previewState.start = false;
  previewState.firstStepsDismissed = false;
  previewState.demo = false;
  previewState.noApps = false;
  previewState.customApps = null;
});

describe("Cockpit bei Ladefehlern", () => {
  it("Serverliste kommt nicht an: keine Entwarnung, sondern der Grund und „Erneut versuchen“", async () => {
    const failing = new Set(["/hosts"]);
    vi.stubGlobal("fetch", fetchWith(failing));
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Lagebild gerade nicht abrufbar"));
    expect(screen.getByTestId("cockpit-status").textContent).not.toContain("Alles läuft rund");
    expect(screen.getByTestId("cockpit-load-error").textContent).toContain("Die Server-Liste ließ sich gerade nicht laden: Datenbank gesperrt");
    const stat = screen.getByTestId("stat-Server").textContent ?? "";
    expect(stat).toContain("gerade nicht abrufbar");
    expect(stat).not.toContain("0/0");
    const attention = screen.getByTestId("attention");
    expect(attention.textContent).not.toContain("Nichts offen");
    expect(within(attention).getByTestId("attention-unknown").textContent).toContain("Unbekannt");

    failing.clear();
    fireEvent.click(within(screen.getByTestId("cockpit-load-error")).getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Alles läuft rund. 9 von 9 Servern online"));
    expect(screen.queryByTestId("cockpit-load-error")).toBeNull();
  });

  it("Übersicht kommt nicht an: Server zählen, aber kein „Alles läuft rund“ und kein ewiges „…“", async () => {
    vi.stubGlobal("fetch", fetchWith(new Set(["/overview"])));
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Dienste, Backups und Meldungen sind gerade nicht abrufbar"));
    const status = screen.getByTestId("cockpit-status").textContent ?? "";
    expect(status).toContain("9 von 9 online");
    expect(status).not.toContain("Alles läuft rund");
    expect(screen.getByTestId("cockpit-load-error").textContent).toContain("Die Übersicht (Dienste, Backups, Meldungen) ließ sich gerade nicht laden");
    for (const label of ["Dienste", "Meldungen", "Freigaben"]) {
      const text = screen.getByTestId(`stat-${label}`).textContent ?? "";
      expect(text).toContain("gerade nicht abrufbar");
      expect(text).not.toContain("…");
    }
    expect(screen.getByTestId("stat-Freigaben").textContent).not.toContain("nichts offen");
    expect(screen.getByTestId("attention").textContent).not.toContain("Nichts offen");
  });

  it("Nachladen scheitert: die letzten Werte bleiben, aber als „nicht aktuell“", async () => {
    const failing = new Set<string>();
    vi.stubGlobal("fetch", fetchWith(failing));
    const client = renderCockpit();
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Alles läuft rund"));

    failing.add("/hosts");
    await act(() => client.refetchQueries({ queryKey: ["hosts", "all"] }));
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Zuletzt keine Störung bekannt"));
    expect(screen.getByTestId("cockpit-status").textContent).toContain("nicht aktuell");
    expect(screen.getByTestId("stat-Server").textContent).toContain("9/9");
    expect(screen.getByTestId("cockpit-load-error").textContent).toContain("Der aktuelle Stand ließ sich gerade nicht laden: Datenbank gesperrt");
    expect(screen.getByTestId("attention-stale").textContent).toContain("nicht aktuell");
    expect(screen.getByTestId("attention").textContent).not.toContain("genieß die Ruhe");
  });

  it("Serverliste veraltet, Übersicht fehlt ganz: der Kasten nennt den Grund der Übersicht", async () => {
    const failing = new Set<string>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = String(input).replace(/^https?:\/\/[^/]+/, "").replace(/^\/api\/v1/, "");
      if (key === "/hosts" && failing.has(key)) return new Response(JSON.stringify({ detail: "Serverliste gesperrt" }), { status: 500 });
      if (key === "/overview") return new Response(JSON.stringify({ detail: "Übersicht gesperrt" }), { status: 500 });
      return fetchWith(new Set())(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = renderCockpit();
    await waitFor(() => expect(screen.getByTestId("cockpit-load-error").textContent).toContain("Übersicht gesperrt"));

    failing.add("/hosts");
    await act(() => client.refetchQueries({ queryKey: ["hosts", "all"] }));
    const box = screen.getByTestId("cockpit-load-error").textContent ?? "";
    expect(box).toContain("Die Übersicht (Dienste, Backups, Meldungen) ließ sich gerade nicht laden: Übersicht gesperrt");
    expect(box).not.toContain("Serverliste gesperrt");
  });

  it("Gerät offline (die Abfrage wartet, ohne Fehler): keine Entwarnung, sondern ein Hinweis", async () => {
    onlineManager.setOnline(false);
    const fetchMock = fetchWith(new Set());
    vi.stubGlobal("fetch", fetchMock);
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Lagebild gerade nicht abrufbar"));
    expect(screen.getByTestId("cockpit-status").textContent).not.toContain("Alles läuft rund");
    expect(screen.getByTestId("cockpit-load-error").textContent).toContain("Dieses Gerät ist gerade offline");
    expect(screen.getByTestId("attention").textContent).not.toContain("Nichts offen");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/hosts"))).toBe(false);
  });
});
