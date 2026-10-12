import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BETA_HINT } from "../../components/BetaBadge";
import { useAuthStore } from "../../state/auth";
import { ExtensionsSettings } from "./ExtensionsSettings";

/** Einstellungen → Erweiterungen: Module mit `stage: "beta"` tragen ein Abzeichen, alle anderen nicht. */
const base = { version: "1.0.0", source: "bundled", description: null, icon: null, granted_permissions: [], last_error: null, state: "disabled" };
let rows: Record<string, unknown>[];

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    if (path === "/extensions") return new Response(JSON.stringify(rows));
    throw new Error(`Unerwarteter Fetch: ${path}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderList() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><ExtensionsSettings /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Einstellungen → Erweiterungen: Beta-Kennzeichen", () => {
  it("zeigt „Beta“ nur bei Modulen mit stage beta und erklärt es einmal unter der Liste", async () => {
    rows = [
      { ...base, id: "system", name: "System", category: "servers", sort_order: 10, stage: "stable" },
      { ...base, id: "gameserver", name: "Gameserver", category: "servers", sort_order: 60, stage: "beta" },
      { ...base, id: "network", name: "Netzwerk", category: "servers", sort_order: 70, stage: "beta" },
      { ...base, id: "alt", name: "Ohne Angabe", category: "tools" },
    ];
    renderList();
    await screen.findByTestId("ext-system");
    expect(within(screen.getByTestId("ext-gameserver")).getByTestId("beta-badge")).toBeInTheDocument();
    expect(within(screen.getByTestId("ext-network")).getByTestId("beta-badge")).toBeInTheDocument();
    expect(within(screen.getByTestId("ext-system")).queryByTestId("beta-badge")).toBeNull();
    expect(within(screen.getByTestId("ext-alt")).queryByTestId("beta-badge")).toBeNull();
    // Die Erklärung steht genau einmal da, nicht bei jedem Modul.
    expect(screen.getAllByTestId("beta-explainer")).toHaveLength(1);
    expect(screen.getByTestId("beta-explainer")).toHaveTextContent(BETA_HINT);
  });

  it("ohne Beta-Modul gibt es weder Abzeichen noch Erklärung", async () => {
    rows = [
      { ...base, id: "system", name: "System", category: "servers", sort_order: 10, stage: "stable" },
      { ...base, id: "alt", name: "Ohne Angabe", category: "tools" },
    ];
    renderList();
    await screen.findByTestId("ext-system");
    expect(screen.queryByTestId("beta-badge")).toBeNull();
    expect(screen.queryByTestId("beta-explainer")).toBeNull();
  });
});
