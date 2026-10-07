import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SocPage } from "./SocPage";

// Ein Audit kann über eine Stunde dauern. Die Seite zeigt sofort „Audit läuft seit …“ (auch nach dem Neuladen und für
// den Lauf nach Zeitplan), ein zweiter Klick zeigt das laufende, und der Reiter „Härtung“ lädt das Ergebnis, sobald es
// fertig ist.

const STARTED = Math.floor(Date.now() / 1000);
/** "14:03": heute angefangen, also nur die Uhrzeit. */
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const RUN = { run_id: "audit_0123456789abcdef", requested_at: STARTED, started_at: STARTED, trigger: "schedule", waiting: false };

function host(extra: Record<string, unknown> = {}) {
  return {
    host_id: "h-docker", host_name: "docker-vm", host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.0.7",
    signature_version: "27410", signature_date: "Wed Sep 24 2026", signature_stale: false, signature_age_days: 1, freshclam_active: true,
    lynis_installed: true, last_scan: null, last_audit: null, scanning: false, auditing: false, audit_run: null, ...extra,
  };
}

function overview(h: Record<string, unknown>) {
  return {
    hosts: [h],
    summary: { hosts: 1, protected: 1, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: null, score: 60 },
    attention: [],
    config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
  };
}

const RESULT = {
  id: "audit_0123456789abcdef", host_id: "h-docker", host_name: "docker-vm", status: "ok", hardening_index: 64,
  warnings: ["SSH-7408: Consider hardening SSH configuration"], suggestions: [], error: null, created_at: STARTED + 30,
};

type Server = { overview: unknown; audits: unknown[]; post?: unknown; calls: string[]; hold?: Promise<never> };

function stubFetch(server: Server) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    server.calls.push(`${method} ${url}`);
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) {
      if (server.hold) return server.hold;
      return json(server.overview);
    }
    if (url.endsWith("/defender/audits") && method === "POST") return json(server.post);
    if (url.endsWith("/defender/audits")) return json(server.audits);
    if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 1, checked: 0, up_to_date: 0, packages: 0, security: 0, reboot: 0 } });
    if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 1, hosts: 1 } });
    if (url.includes("/defender/events") || url.includes("/defender/findings") || url.includes("/defender/scans")) return json([]);
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
}

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/shield/soc");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("Nodvard Shield: Härtungs-Audit, das lange läuft", () => {
  it("zeigt in der Server-Zeile, seit wann das Audit läuft (auch nach dem Neuladen, auch nach Zeitplan)", async () => {
    stubFetch({ overview: overview(host({ auditing: true, audit_run: RUN })), audits: [], calls: [] });
    render(<SocPage />);
    const row = within(await screen.findByTestId("host-h-docker"));
    expect(row.getByTestId("audit-running")).toHaveTextContent(`Audit läuft seit ${clock(STARTED)}`);
    // Der Knopf bleibt klickbar: ein Klick zeigt das laufende Audit, statt nichts zu tun.
    expect(row.getByRole("button", { name: "Audit" })).toBeEnabled();
  });

  it("wartet ein Audit auf einen freien Platz, steht das so da", async () => {
    stubFetch({ overview: overview(host({ auditing: true, audit_run: { ...RUN, started_at: null, waiting: true } })), audits: [], calls: [] });
    render(<SocPage />);
    const row = within(await screen.findByTestId("host-h-docker"));
    expect(row.getByTestId("audit-running")).toHaveTextContent(`Audit wartet seit ${clock(STARTED)}`);
  });

  it("ein Klick auf „Audit“ zeigt sofort „läuft seit …“, ein zweiter startet kein zweites", async () => {
    const server: Server = {
      overview: overview(host()), audits: [], calls: [],
      post: { hosts: 1, started: ["h-docker"], running: [], audits: [{ host_id: "h-docker", host_name: "docker-vm", ...RUN, trigger: "manual", already_running: false }] },
    };
    stubFetch(server);
    render(<SocPage />);
    const row = within(await screen.findByTestId("host-h-docker"));
    expect(row.queryByTestId("audit-running")).toBeNull();

    // Die neue Übersicht kommt (noch) nicht: Die Zeile zeigt den Lauf trotzdem sofort.
    server.hold = new Promise<never>(() => undefined);
    fireEvent.click(row.getByRole("button", { name: "Audit" }));
    await waitFor(() => expect(row.getByTestId("audit-running")).toHaveTextContent(`Audit läuft seit ${clock(STARTED)}`));
    expect(screen.getByRole("status")).toHaveTextContent(`Härtungs-Audit auf „docker-vm“: Audit läuft seit ${clock(STARTED)}.`);
    expect(server.calls.filter((c) => c.startsWith("POST") && c.endsWith("/defender/audits"))).toHaveLength(1);

    server.post = { hosts: 1, started: [], running: ["h-docker"], audits: [{ host_id: "h-docker", host_name: "docker-vm", ...RUN, already_running: true }] };
    fireEvent.click(row.getByRole("button", { name: "Audit" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(
      `Auf „docker-vm“ läuft schon ein Audit (seit ${clock(STARTED)}); es wurde kein zweites gestartet.`,
    ));
    expect(row.getByTestId("audit-running")).toHaveTextContent(`Audit läuft seit ${clock(STARTED)}`);
  });

  it("Reiter „Härtung“: zeigt das laufende Audit und lädt das Ergebnis, sobald es fertig ist", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      window.history.replaceState(null, "", "/ext/shield/soc?tab=hardening");
      const server: Server = { overview: overview(host({ auditing: true, audit_run: RUN })), audits: [], calls: [] };
      stubFetch(server);
      render(<SocPage />);
      const box = within(await screen.findByTestId("audits-running"));
      expect(box.getByText("docker-vm")).toBeInTheDocument();
      expect(box.getByTestId("audit-running")).toHaveTextContent(`Audit läuft seit ${clock(STARTED)}`);
      expect(screen.queryByText("Noch kein Härtungs-Audit")).toBeNull();

      // Lynis ist fertig: Die nächste Abfrage der Übersicht meldet kein laufendes Audit mehr.
      server.overview = overview(host({ last_audit: { status: "ok", hardening_index: 64, warnings: 1, created_at: STARTED + 30, error: null } }));
      server.audits = [RESULT];
      await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
      await waitFor(() => expect(screen.getByText(/SSH-7408: Consider hardening SSH configuration/)).toBeInTheDocument());
      expect(screen.queryByTestId("audits-running")).toBeNull();
      expect(screen.getByText("64")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
