import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { RouteErrorPage } from "./ErrorPages";
import { routes } from "./router";

vi.mock("../lib/ws", () => ({
  wsClient: { ensureConnected: vi.fn(), disconnect: vi.fn() },
  useWsSubscription: vi.fn(),
}));

const USER = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] };

function stubApi() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    if (path === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
    return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
  }));
}

function renderApp(entry: string, tree: RouteObject[] = routes) {
  const router = createMemoryRouter(tree, { initialEntries: [entry] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function Boom({ message }: { message: string }): never {
  throw new Error(message);
}

beforeEach(() => {
  // React Router und React melden einen abgefangenen Fehler per console.error -- hier erwartet.
  vi.spyOn(console, "error").mockImplementation(() => {});
  stubApi();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: null });
});

describe("unbekannte Adresse", () => {
  it("angemeldet: deutsche Seite „Seite nicht gefunden“ mit Knopf zur Übersicht, das Menü bleibt", async () => {
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
    renderApp("/gibt-es-nicht");

    expect(await screen.findByText("Seite nicht gefunden")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zur Übersicht" })).toHaveAttribute("href", "/");
    // Keine Entwicklerseite von React Router.
    expect(screen.queryByText(/Unexpected Application Error/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Hey developer/i)).not.toBeInTheDocument();
    // Die Oberfläche (Menü, Name des angemeldeten Benutzers) ist noch da.
    expect(await screen.findByText("Nico")).toBeInTheDocument();
  });

  it("auch tief verschachtelte unbekannte Adressen landen dort", async () => {
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
    renderApp("/settings/gibt-es-nicht-2/noch-tiefer");
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Seite nicht gefunden");
  });

  it("nicht angemeldet: erst zur Anmeldung, die Adresse wird für danach gemerkt", async () => {
    useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: null });
    const router = renderApp("/gibt-es-nicht");
    expect(await screen.findByRole("button", { name: "Anmelden" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect((router.state.location.state as { from?: string }).from).toBe("/gibt-es-nicht");
  });

  it("die Übersicht (/) ist keine unbekannte Adresse", async () => {
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
    renderApp("/");
    await screen.findByText("Nico");
    expect(screen.queryByText("Seite nicht gefunden")).not.toBeInTheDocument();
  });
});

describe("Fehler beim Aufbauen einer Seite", () => {
  it("zeigt eine deutsche Fehlerseite mit „Seite neu laden“ und „Zur Übersicht“, Einzelheiten zugeklappt", async () => {
    renderApp("/kaputt", [
      { errorElement: <RouteErrorPage />, children: [{ path: "/kaputt", element: <Boom message="x is undefined" /> }] },
    ]);
    const box = await screen.findByTestId("route-error");
    expect(box).toHaveAttribute("role", "alert");
    expect(box).toHaveTextContent("Hier ist etwas schiefgegangen");
    expect(screen.getByRole("button", { name: "Seite neu laden" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zur Übersicht" })).toHaveAttribute("href", "/");
    expect(screen.getByText("Technische Einzelheiten").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("x is undefined")).toBeInTheDocument();
    expect(screen.queryByText(/Hey developer/i)).not.toBeInTheDocument();
  });

  it("nachgeladene Datei fehlt (nach einem Update): Hinweis aufs Neuladen", async () => {
    renderApp("/kaputt", [
      {
        errorElement: <RouteErrorPage />,
        children: [{ path: "/kaputt", element: <Boom message="Failed to fetch dynamically imported module: /assets/Terminal-ab12.js" /> }],
      },
    ]);
    expect(await screen.findByText("Die Seite konnte nicht geladen werden")).toBeInTheDocument();
    expect(screen.getByText(/gerade aktualisiert/)).toBeInTheDocument();
  });

  it("ein 404 aus dem Router zeigt dieselbe Seite wie die *-Route", async () => {
    renderApp("/weg", [
      {
        errorElement: <RouteErrorPage fullScreen />,
        children: [{ path: "/weg", loader: () => { throw new Response("", { status: 404, statusText: "Not Found" }); }, element: <p>nie</p> }],
      },
    ]);
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Seite nicht gefunden");
    expect(screen.queryByText("Technische Einzelheiten")).not.toBeInTheDocument();
  });

  it("andere Fehlerantworten (500) zeigen die allgemeine Fehlerseite mit Statuszeile", async () => {
    renderApp("/weg", [
      {
        errorElement: <RouteErrorPage fullScreen />,
        children: [{ path: "/weg", loader: () => { throw new Response("", { status: 500, statusText: "Server Error" }); }, element: <p>nie</p> }],
      },
    ]);
    expect(await screen.findByText("Hier ist etwas schiefgegangen")).toBeInTheDocument();
    expect(screen.getByText("500 Server Error")).toBeInTheDocument();
  });

  it("ein Fehler in einer Seite der Oberfläche lässt das Menü stehen (Fehlerseite liegt in der AppShell)", async () => {
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
    // Dieselbe Verschachtelung wie in router.tsx, nur mit einer Seite, die abstürzt.
    const appShellRoute = routes[0].children!.find((r) => r.element && !r.path)!.children![0];
    const innerGroup = appShellRoute.children![0];
    expect(innerGroup.errorElement).toBeTruthy();
    renderApp("/kaputt", [
      {
        element: appShellRoute.element,
        children: [{ errorElement: innerGroup.errorElement, children: [{ path: "/kaputt", element: <Boom message="kaputt" /> }] }],
      },
    ]);
    expect(await screen.findByTestId("route-error")).toHaveTextContent("Hier ist etwas schiefgegangen");
    expect(await screen.findByText("Nico")).toBeInTheDocument();
  });
});
