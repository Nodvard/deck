import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ContainerWatch } from "./ContainerWatch";
import { SocPage } from "./SocPage";

/** Stolperstellen: Ladefehler sind keine leere Liste, Großaktionen fragen nach, Fehler mit Grund. */

const OVERVIEW = {
  hosts: [
    { host_id: "h1", host_name: "pi", host_status: "up", reachable: true, clamav_installed: true, lynis_installed: true, last_scan: null, last_audit: null, scanning: false, auditing: false },
    { host_id: "h2", host_name: "docker", host_status: "up", reachable: true, clamav_installed: true, lynis_installed: true, last_scan: null, last_audit: null, scanning: false, auditing: false },
  ],
  summary: { hosts: 2, protected: 2, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: null, score: 80 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

type Call = { url: string; method: string };

/** `broken`: Teilpfade, die mit einem Serverfehler antworten, bis `heal()` aufgerufen wird. */
function mockFetch(calls: Call[], broken: string[] = []) {
  const failing = new Set(broken);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    for (const part of failing) {
      if (url.includes(part) && method === "GET") return json({ detail: "Datenbank gerade gesperrt." }, 500);
    }
    if (url.includes("/defender/overview")) return json(OVERVIEW);
    if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 2, checked: 2, up_to_date: 2, packages: 0, security: 0, reboot: 0 } });
    if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 2, hosts: 2 } });
    if (url.includes("/defender/findings") && method === "GET") return json([]);
    if (url.includes("/defender/scans") && method === "GET") return json([]);
    if (url.includes("/defender/audits") && method === "GET") return json([]);
    if (url.includes("/defender/scans") && method === "POST") return json({ scans: ["s1", "s2"] });
    if (url.includes("/defender/audits") && method === "POST") return json({ audits: [], skipped: [] });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
  return Object.assign(fetchMock, { heal: () => failing.clear() });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

function openTab(tab: string) {
  window.history.replaceState(null, "", `/ext/shield/soc?tab=${tab}`);
}

describe("Ladefehler sind keine Entwarnung", () => {
  it("Quarantäne: sagt den Grund statt „Keine Funde“ und lädt auf Wunsch neu", async () => {
    openTab("quarantine");
    const calls: Call[] = [];
    const fetchMock = mockFetch(calls, ["/defender/findings"]);
    vi.stubGlobal("fetch", fetchMock);
    render(<SocPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Die Liste der Funde ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    expect(alert).toHaveTextContent("Das heißt nicht, dass alles in Ordnung ist.");
    expect(screen.queryByText("Keine Funde")).toBeNull();

    fetchMock.heal();
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Keine Funde")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });

  it("Härtung: kein „Noch kein Härtungs-Audit“, wenn die Ergebnisse nicht ladbar sind", async () => {
    openTab("hardening");
    const fetchMock = mockFetch([], ["/defender/audits"]);
    vi.stubGlobal("fetch", fetchMock);
    render(<SocPage />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Liste der Härtungs-Audits ließ sich gerade nicht laden");
    expect(screen.queryByText("Noch kein Härtungs-Audit")).toBeNull();
    fetchMock.heal();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Noch kein Härtungs-Audit")).toBeInTheDocument();
  });

  it("Scans: kein „Noch keine Scans“, wenn der Verlauf nicht ladbar ist", async () => {
    openTab("scans");
    vi.stubGlobal("fetch", mockFetch([], ["/defender/scans"]));
    render(<SocPage />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Der Verlauf ließ sich gerade nicht laden");
    expect(screen.queryByText("Noch keine Scans.")).toBeNull();
  });
});

describe("Tiefenscan und Audit auf allen Servern fragen nach", () => {
  it("Tiefenscan: Rückfrage nennt die Zahl der Server, Abbrechen startet nichts", async () => {
    openTab("overview");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<SocPage />);
    await screen.findByText("Geschützte Server");
    confirmDialog.mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: /Tiefenscan/ }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    expect(confirmDialog.mock.calls[0][0]).toContain("Tiefenscan auf allen 2 Servern starten?");
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /Tiefenscan/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/defender/scans") && c.method === "POST")).toBe(true));
  });

  it("Härtungs-Audit: erst nach der Rückfrage", async () => {
    openTab("overview");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<SocPage />);
    await screen.findByText("Geschützte Server");
    fireEvent.click(screen.getByRole("button", { name: /Härtungs-Audit/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/defender/audits") && c.method === "POST")).toBe(true));
    expect(confirmDialog.mock.calls[0][0]).toContain("Härtungs-Audit auf allen 2 Servern starten?");
  });
});

describe("Container-Wache: Fehler mit Grund", () => {
  const INCIDENT = {
    id: "inc-1", host_name: "docker", target: "nginx", message: "Container CRASH", created_at: 1750000000, status: "open",
    status_label: "Offen", status_changed_at: null, ai_summary: null, action_id: null, is_crash: true,
  };

  it("zeigt den Grund vom Server statt „HTTP 500“ und meldet einen gescheiterten Statuswechsel", async () => {
    let historyBroken = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
      if (url.includes("/ext/shield/history")) {
        return historyBroken ? json({ detail: "Verlauf gerade nicht lesbar." }, 500) : json({ items: [INCIDENT], total: 1, hosts: ["docker"] });
      }
      if (url.endsWith("/ext/shield/stats")) return json({ by_status: { open: 1 }, pending_batch: 0, watched_hosts: 1, ai_healthy: true, ai_message: "ok" });
      if (url.endsWith("/incidents/inc-1/resolve") && method === "POST") return json({ detail: "Dafür fehlt dir das Recht." }, 403);
      throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    }));
    const { unmount } = render(<ContainerWatch />);
    expect(await screen.findByText("Fehler: Verlauf gerade nicht lesbar.")).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 500/)).toBeNull();
    unmount();

    historyBroken = false;
    render(<ContainerWatch />);
    const row = await screen.findByTestId("incident-inc-1");
    fireEvent.click(within(row).getByRole("button", { name: "Erledigt" }));
    expect(await screen.findByText("Status ließ sich nicht ändern: Dafür fehlt dir das Recht.")).toBeInTheDocument();
  });
});
