/**
 * Ende-zu-Ende durch die echte Kette: Router (Browser-Verlauf) -> ExtensionPage (Kern, feuert
 * `nodvard-deck:navigate` und `lattice:navigate`) -> useUrlParams -> SocPage. Die Einzelteile haben eigene Tests
 * (ExtensionPage.test.tsx, location.test.tsx, SocPage.test.tsx); hier wird geprüft, dass sie
 * zusammen halten -- Links aus Menü und Benachrichtigungen, Klicks im Reiterstreifen, Zurück/Vor.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, RouterProvider, createBrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExtensionPage } from "../../../../frontend/src/routes/ExtensionPage";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "shield", path: "/soc", component: "SocPage" }], isLoading: false }),
}));
// ExtensionPage laedt das Bundle per import(url) -- hier durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/shield/frontend/index.js?v=dev", async () => ({ SocPage: (await import("./SocPage")).SocPage }));

const HOST = { clamav_installed: false, lynis_installed: false, last_scan: null, last_audit: null, scanning: false, auditing: false, host_status: "up", reachable: true };
const OVERVIEW = {
  hosts: [
    { host_id: "h-pi", host_name: "Raspberry Pi", ...HOST },
    { host_id: "h-docker", host_name: "docker", ...HOST },
  ],
  summary: { hosts: 2, protected: 0, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: null, score: 50 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) return json(OVERVIEW);
    if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 2, checked: 2, up_to_date: 2, packages: 0, security: 0, reboot: 0 } });
    if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 0, hosts: 2 } });
    if (url.includes("/defender/events")) return json([]);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

/** Menü und Benachrichtigungen: Links auf die (schon offene) Seite. */
function Menu() {
  return (
    <nav>
      <Link to="/ext/shield/soc">Menü Nodvard Shield</Link>
      <Link to="/ext/shield/soc?tab=guard&host=h-pi">Einbruchschutz Raspberry Pi</Link>
    </nav>
  );
}

function renderApp() {
  const router = createBrowserRouter([{ path: "/ext/:extId/*", element: <><Menu /><ExtensionPage /></> }]);
  render(<RouterProvider router={router} />);
}

const selected = (name: RegExp) => expect(screen.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/shield/soc");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(async () => {
  // noch laufende Abrufe abwarten, bevor aufgeräumt wird (sonst "not wrapped in act")
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  vi.unstubAllGlobals();
});

describe("Nodvard Shield in der echten Kern-Shell", () => {
  it("Link, Klick im Reiterstreifen, derselbe Link erneut: der Reiter folgt jedes Mal", async () => {
    renderApp();
    await screen.findByTestId("host-h-pi");
    selected(/Übersicht/);

    click("Einbruchschutz Raspberry Pi");
    await waitFor(() => selected(/Einbruchschutz/));
    expect(screen.getByTestId("host-filter")).toBeInTheDocument();

    // Im Streifen umschalten: die Seite schreibt die Adresse selbst, der Router erfährt davon nichts.
    fireEvent.click(screen.getByRole("tab", { name: /Updates/ }));
    selected(/Updates/);
    expect(window.location.search).toBe("?tab=updates&host=h-pi");

    // Derselbe Link: für den Router ändert sich nichts, die Seite muss trotzdem wieder umschalten.
    click("Einbruchschutz Raspberry Pi");
    await waitFor(() => selected(/Einbruchschutz/));
    expect(screen.getByTestId("host-filter")).toBeInTheDocument();
    expect(window.location.search).toBe("?tab=guard&host=h-pi");
  });

  it("der Menülink ohne Query führt auch dann zur Übersicht ohne Filter, wenn die Adresse dieselbe ist", async () => {
    renderApp();
    await screen.findByTestId("host-h-pi");
    click("Menü Nodvard Shield"); // Router hält genau diese Adresse schon für aktuell
    fireEvent.click(screen.getByRole("tab", { name: /Härtung/ }));
    selected(/Härtung/);

    click("Menü Nodvard Shield");
    await waitFor(() => selected(/Übersicht/));
    expect(window.location.search).toBe("");
    expect(screen.queryByTestId("host-filter")).toBeNull();

    click("Einbruchschutz Raspberry Pi");
    await waitFor(() => selected(/Einbruchschutz/));
    click("Menü Nodvard Shield");
    await waitFor(() => selected(/Übersicht/));
    expect(screen.queryByTestId("host-filter")).toBeNull();
    expect(await screen.findByTestId("host-h-docker")).toBeInTheDocument();
  });

  it("Zurück und Vor folgen, auch nach einem Klick im Reiterstreifen", async () => {
    renderApp();
    await screen.findByTestId("host-h-pi");
    click("Einbruchschutz Raspberry Pi");
    await waitFor(() => selected(/Einbruchschutz/));
    fireEvent.click(screen.getByRole("tab", { name: /Updates/ })); // ersetzt den Verlaufseintrag
    click("Menü Nodvard Shield");
    await waitFor(() => selected(/Übersicht/));

    act(() => window.history.back());
    await waitFor(() => selected(/Updates/));
    expect(window.location.search).toBe("?tab=updates&host=h-pi");
    expect(screen.getByTestId("host-filter")).toBeInTheDocument();

    act(() => window.history.forward());
    await waitFor(() => selected(/Übersicht/));
    expect(screen.queryByTestId("host-filter")).toBeNull();
  });
});
