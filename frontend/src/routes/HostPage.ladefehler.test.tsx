import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { HostPage } from "./HostPage";

vi.mock("../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

/** Seiten-Durchgang: Ein Ladefehler ist weder "Server nicht gefunden" noch "Noch keine Werkzeuge". */

const HOST = "g-docker";

/** Wie die Design-Vorschau; Pfade in `broken` antworten mit 500 und einem Grund, bis `heal()`. */
function mockFetch(broken: Record<string, string>) {
  const failing = new Map(Object.entries(broken));
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    const reason = failing.get(key);
    if (reason) return new Response(JSON.stringify({ detail: reason }), { status: 500 });
    const body = respond(path, init?.method ?? "GET");
    if (body === undefined) return new Response(JSON.stringify({ detail: "nicht da" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: 200 });
  });
  return Object.assign(fetchMock, { heal: () => failing.clear() });
}

function renderHost(hostId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hosts/${hostId}`]}>
        <Routes>
          <Route path="/hosts/:hostId" element={<HostPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
});

describe("HostPage bei Ladefehlern", () => {
  it("Server nicht ladbar: Grund statt „Server nicht gefunden“, erneut versuchen", async () => {
    const fetchMock = mockFetch({ [`/hosts/${HOST}`]: "Datenbank gerade gesperrt." });
    vi.stubGlobal("fetch", fetchMock);
    renderHost(HOST);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Server ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    expect(screen.queryByText("Server nicht gefunden.")).toBeNull();
    fetchMock.heal();
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("ein unbekannter Server heißt weiter „nicht gefunden“", async () => {
    vi.stubGlobal("fetch", mockFetch({}));
    renderHost("gibt-es-nicht");
    expect(await screen.findByText("Server nicht gefunden.")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });

  it("Werkzeuge nicht ladbar: Grund statt „Noch keine Werkzeuge“ samt Hinweis auf Module", async () => {
    const fetchMock = mockFetch({ [`/hosts/${HOST}/tools`]: "Erweiterungen laden noch." });
    vi.stubGlobal("fetch", fetchMock);
    renderHost(HOST);
    expect(await screen.findByText("Die Liste der Werkzeuge ließ sich gerade nicht laden: Erweiterungen laden noch.")).toBeInTheDocument();
    expect(screen.queryByTestId("host-no-tools")).toBeNull();
    fetchMock.heal();
    fireEvent.click(within(screen.getByText(/Die Liste der Werkzeuge/).closest("[data-testid='load-error']") as HTMLElement).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("host-tools")).toBeInTheDocument();
    expect(screen.queryByText(/Die Liste der Werkzeuge/)).toBeNull();
  });

  it("Aktionen und Auslastung nicht ladbar: der Grund steht da, statt dass sie still fehlen", async () => {
    vi.stubGlobal("fetch", mockFetch({
      [`/hosts/${HOST}/actions`]: "Gate nicht bereit.",
      [`/hosts/${HOST}/metrics`]: "Messung hängt.",
    }));
    renderHost(HOST);
    expect(await screen.findByText("Die Liste der Aktionen ließ sich gerade nicht laden: Gate nicht bereit.")).toBeInTheDocument();
    expect(await screen.findByText("Die Auslastung ließ sich gerade nicht laden: Messung hängt.")).toBeInTheDocument();
    expect(screen.queryByTestId("host-metrics-hint")).toBeNull();
  });
});
