import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SocPage, hostScanBadge } from "./SocPage";

/** Die Spalte „Letzter Scan“ richtet sich nach denselben Funden wie die Zahl oben: nach Quarantäne oder Löschen
 * steht dort nicht mehr „Bedrohung gefunden“, während oben „keine Bedrohung“ steht. */

const now = Date.now() / 1000;
const scan = (host: string, status: string) => ({
  id: `s-${host}`, host_id: host, host_name: host, kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "manual",
  status, files_scanned: 10, infected: status === "infected" ? 1 : 0, error: null, output_tail: null, started_at: now - 600, finished_at: now - 590,
});
const host = (id: string, extra: Record<string, unknown>) => ({
  host_id: id, host_name: id, host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.4.3",
  signature_version: "27800", signature_date: "Wed Oct 7 2026", freshclam_active: true, lynis_installed: false,
  last_audit: null, scanning: false, auditing: false, ...extra,
});

const OVERVIEW = {
  hosts: [
    host("quarantaene", { last_scan: scan("quarantaene", "infected"), open_findings: 0, last_scan_outcome: "quarantined" }),
    host("geloescht", { last_scan: scan("geloescht", "infected"), open_findings: 0, last_scan_outcome: "cleaned" }),
    host("waechter", { last_scan: null, open_findings: 2, last_scan_outcome: null }),
    host("harmlos", { last_scan: scan("harmlos", "infected"), open_findings: 0, last_scan_outcome: "accepted" }),
    host("beides", { last_scan: scan("beides", "infected"), open_findings: 1, last_scan_outcome: "open" }),
  ],
  summary: { hosts: 5, protected: 5, open_threats: 3, quarantined: 1, neutralized_total: 2, findings_30d: 4, avg_hardening: null, score: 60 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/shield/soc");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("Spalte „Letzter Scan“", () => {
  it("nach Quarantäne, nach dem Löschen und bei offenen Funden des Wächters", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
      if (url.includes("/defender/overview")) return json(OVERVIEW);
      if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 3, checked: 0, up_to_date: 0, packages: 0, security: 0, reboot: 0 } });
      if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 0, hosts: 3 } });
      if (url.includes("/defender/findings") || url.includes("/defender/scans") || url.includes("/defender/events")) return json([]);
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
    render(<SocPage />);
    const row = async (name: string) => (await screen.findByText(name, { selector: "td *" })).closest("tr") as HTMLElement;
    expect(within(await row("quarantaene")).getByText("in Quarantäne")).toBeInTheDocument();
    expect(within(await row("geloescht")).getByText("bereinigt")).toBeInTheDocument();
    expect(within(await row("waechter")).getByText("2 offene Bedrohungen")).toBeInTheDocument();
    expect(within(await row("waechter")).getByText("Details unter „Quarantäne“")).toBeInTheDocument();
    expect(within(await row("harmlos")).getByText("als harmlos markiert")).toBeInTheDocument();
    // Offener Fund und befallener Scan: die Zahl, und kein Hinweis, der Fund stamme aus genau diesem Scan.
    expect(within(await row("beides")).getByText("1 offene Bedrohung")).toBeInTheDocument();
    expect(within(await row("beides")).queryByText(/Schnellscan/)).toBeNull();
    expect(screen.queryByText("Bedrohung gefunden")).toBeNull();
  });

  it("ohne Angaben vom Server (ältere Version) bleibt es beim Ergebnis des Scans", () => {
    expect(hostScanBadge({ last_scan: scan("x", "infected") as never })).toEqual({ label: "Bedrohung gefunden", tone: "bad" });
    expect(hostScanBadge({ last_scan: scan("x", "infected") as never, open_findings: 0, last_scan_outcome: null }))
      .toEqual({ label: "Bedrohung gefunden", tone: "bad" });
    expect(hostScanBadge({ last_scan: scan("x", "clean") as never, open_findings: 1 })).toEqual({ label: "1 offene Bedrohung", tone: "bad" });
    expect(hostScanBadge({ last_scan: scan("x", "infected") as never, open_findings: 0, last_scan_outcome: "open" }))
      .toEqual({ label: "Bedrohung gefunden", tone: "bad" });
    expect(hostScanBadge({ last_scan: null })).toBeNull();
  });
});
