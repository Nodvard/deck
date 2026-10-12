import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BETA_HINT } from "../components/BetaBadge";
import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { AppShell } from "./AppShell";

vi.mock("../lib/ws", () => ({
  wsClient: { ensureConnected: vi.fn(), disconnect: vi.fn() },
  useWsSubscription: vi.fn(),
}));

/** Menü: Seiten von Modulen mit `stage: "beta"` tragen hinter dem Namen das Abzeichen „Beta“, alle anderen nicht.
 * Die Seitenliste kommt aus den Vorschau-Daten (dort ist „Gameserver“ Beta, „Proxmox-Backups“ nicht). */
describe("AppShell: Beta-Abzeichen im Menü", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("kennzeichnet nur Beta-Module und zeigt „Proxmox-Backups“ unter dem neuen Namen", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET") ?? {}), { status: 200 });
    }));
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
      status: "authenticated",
      mfaToken: null,
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route element={<AppShell />}><Route path="/" element={<p>Startseite</p>} /></Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const nav = await screen.findByRole("navigation", { name: "Hauptnavigation" });
    const gameserver = await within(nav).findByRole("link", { name: /Gameserver/ });
    expect(within(gameserver).getByTestId("beta-badge")).toHaveAttribute("title", BETA_HINT);
    // Der Name des Links bleibt kurz: Vorlese-Programme sagen „Gameserver Beta“, nicht den ganzen Satz.
    expect(gameserver).toHaveAccessibleName(/^Gameserver\s*Beta$/);
    const backups = within(nav).getByRole("link", { name: /Proxmox-Backups/ });
    expect(within(backups).queryByTestId("beta-badge")).toBeNull();
    expect(within(nav).getAllByTestId("beta-badge")).toHaveLength(1);
  });
});
