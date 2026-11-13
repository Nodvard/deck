import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentType } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { AppShell } from "./AppShell";
import { SystemSettings } from "./settings/SystemSettings";

// Die Erweiterung wird mit ihrem eigenen tsconfig geprüft.
const dockerPages = import.meta.glob<{ ContainersPage: ComponentType }>(
  "../../../extensions/docker/frontend/src/ContainersPage.tsx", { eager: true },
);
const { ContainersPage } = Object.values(dockerPages)[0];

vi.mock("../lib/ws", () => ({
  wsClient: { ensureConnected: vi.fn(), disconnect: vi.fn() },
  useWsSubscription: vi.fn(),
}));

beforeEach(() => {
  window.localStorage.clear();
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn(),
    promptDialog: vi.fn(),
    hasPermission: () => true,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    return Response.json(respond(path, init?.method ?? "GET") ?? {});
  }));
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function DockerPage() {
  const { search } = useLocation();
  return <><ContainersPage key={search} /><Link to="?tab=images">Images anzeigen</Link></>;
}

async function renderShell(path = "/") {
  window.history.replaceState(null, "", path);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<>
              <Link to="/ext/docker/matrix?tab=stacks">Docker öffnen</Link>
              <Link to="/settings/system">Update-Zentrale öffnen</Link>
            </>} />
            <Route path="/ext/docker/matrix" element={<DockerPage />} />
            <Route path="/settings/system" element={<SystemSettings />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Nico");
  return screen.getByRole("main");
}

it.each([
  ["Docker öffnen", "Docker"],
  ["Update-Zentrale öffnen", "System"],
])("%s beginnt am Seitenanfang", async (link, heading) => {
  const main = await renderShell();
  main.scrollTop = 240;
  main.scrollLeft = 80;
  fireEvent.click(screen.getByRole("link", { name: link }));
  await screen.findByRole("heading", { name: heading });
  await waitFor(() => {
    expect(main.scrollTop).toBe(0);
    expect(main.scrollLeft).toBe(0);
  });
  expect(screen.getByRole("main")).toBe(main);
});

it("ein Reiterwechsel behält die Scrollposition", async () => {
  const main = await renderShell("/ext/docker/matrix?tab=stacks");
  main.scrollTop = 240;
  main.scrollLeft = 80;
  fireEvent.click(screen.getByRole("link", { name: "Images anzeigen" }));
  expect(window.location.search).toBe("?tab=images");
  expect(main.scrollTop).toBe(240);
  expect(main.scrollLeft).toBe(80);
});
