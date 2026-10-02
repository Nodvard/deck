import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SocPage } from "./SocPage";

const NOW = Date.now() / 1000;
const SCAN = {
  id: "s1", host_id: "h-pi", host_name: "Pi", kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "schedule",
  status: "clean", files_scanned: 10, infected: 0, error: null, output_tail: null, started_at: NOW - 600, finished_at: null,
};

function host(id: string, extra: Record<string, unknown>) {
  return {
    host_id: id, host_name: id, host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.0.7",
    signature_version: "27410", signature_date: "Wed Aug 26 08:23:12 2026", freshclam_active: false, lynis_installed: true,
    last_scan: SCAN, last_audit: null, scanning: false, auditing: false, ...extra,
  };
}

const ATTENTION = {
  kind: "signatures", tone: "warn", host_id: "h-pi", host_name: "h-pi", title: "h-pi: Virensignaturen sind 5 Wochen alt",
  hint: "Mit alten Signaturen erkennt ClamAV neue Schadprogramme nicht.", action_label: "Signaturen aktualisieren",
};

const OVERVIEW = {
  hosts: [
    host("h-pi", { signature_stale: true, signature_age_days: 37, last_audit: null }),
    host("h-ok", {
      signature_stale: false, signature_age_days: 1, freshclam_active: true,
      last_audit: { status: "error", hardening_index: null, warnings: 0, created_at: NOW - 3 * 3600, error: "Lynis: keine Rechte für /etc" },
      last_ok_audit: { status: "ok", hardening_index: 72, warnings: 4, created_at: NOW - 2 * 86400, error: null },
    }),
    host("h-fail", {
      signature_stale: false, signature_age_days: 1, freshclam_active: true,
      last_audit: { status: "error", hardening_index: null, warnings: 0, created_at: NOW - 5 * 3600, error: "Zeitüberschreitung" },
    }),
  ],
  summary: { hosts: 3, protected: 3, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: 72, score: 58, stale_signatures: 1 },
  attention: [ATTENTION],
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/nexus-soc/soc");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

const AUDITS = [
  {
    id: "au-ok", host_id: "h-ok", host_name: "h-ok", status: "ok", hardening_index: 72, warnings: ["SSH erlaubt Root-Login"],
    suggestions: ["Passwortregeln verschärfen"], error: null, created_at: NOW - 2 * 86400,
  },
  {
    id: "au-fail", host_id: "h-fail", host_name: "h-fail", status: "error", hardening_index: null, warnings: [],
    suggestions: [], error: "Zeitüberschreitung", created_at: NOW - 5 * 3600,
  },
];

function stubFetch(calls: string[] = [], overview: unknown = OVERVIEW) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) return json(overview);
    if (url.includes("/defender/audits")) return json(AUDITS);
    if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 3, checked: 0, up_to_date: 0, packages: 0, security: 0, reboot: 0 } });
    if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 3, hosts: 3 } });
    if (url.includes("/defender/events") || url.includes("/defender/findings") || url.includes("/defender/scans")) return json([]);
    if (url.endsWith("/hosts/h-pi/install")) return json({ action_id: "a1", status: "proposed" });
    if (url.endsWith("/actions/a1/approve")) return json({ status: "succeeded", result: {} });
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
}

describe("Nodvard Shield: Signaturen und Härtung", () => {
  it("zeigt veraltete Signaturen unter „Braucht Aufmerksamkeit“ mit Knopf zum Aktualisieren", async () => {
    const calls: string[] = [];
    stubFetch(calls);
    render(<SocPage />);
    const box = within(await screen.findByTestId("shield-attention"));
    expect(box.getByText("h-pi: Virensignaturen sind 5 Wochen alt")).toBeInTheDocument();
    expect(within(screen.getByTestId("host-h-pi")).getByText(/5 Wochen alt/)).toBeInTheDocument();
    expect(within(screen.getByTestId("host-h-ok")).queryByText(/Wochen alt/)).toBeNull();
    fireEvent.click(box.getByRole("button", { name: "Signaturen aktualisieren" }));
    await waitFor(() => expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/hosts/h-pi/install"))).toBe(true));
    await waitFor(() => expect(calls.some((c) => c.endsWith("/actions/a1/approve"))).toBe(true));
  });

  it("mit ?host= stehen nur Hinweise zu diesem Server da", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?host=h-ok");
    stubFetch();
    render(<SocPage />);
    await screen.findByTestId("host-h-ok");
    expect(screen.queryByTestId("shield-attention")).toBeNull();
  });

  it("Härtung: gescheitertes Audit mit Zeitangabe, Fehler im Tooltip, früheres Ergebnis bleibt", async () => {
    stubFetch();
    render(<SocPage />);
    const ok = within(await screen.findByTestId("host-h-ok"));
    expect(ok.getByText("72")).toBeInTheDocument();
    const note = ok.getByTestId("audit-failed");
    expect(note).toHaveTextContent("Letztes Audit fehlgeschlagen (vor 3 Std.)");
    expect(note).toHaveAttribute("title", "Lynis: keine Rechte für /etc");

    const fail = within(screen.getByTestId("host-h-fail"));
    expect(fail.getByTestId("audit-failed")).toHaveTextContent("Letztes Audit fehlgeschlagen (vor 5 Std.)");
    expect(fail.queryByText("Zeitüberschreitung")).toBeNull();
  });

  it("Härtungs-Reiter: gescheitertes Audit heißt „Versucht“ und zeigt Zeitangabe und Fehlertext, ein erfolgreiches „Geprüft“", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=hardening");
    stubFetch();
    render(<SocPage />);
    await screen.findByText("Zeitüberschreitung");
    const failedCard = screen.getByText("h-fail").closest("section") as HTMLElement;
    const okCard = screen.getByText("h-ok").closest("section") as HTMLElement;
    expect(screen.getByText("Letztes Audit fehlgeschlagen (vor 5 Std.)")).toBeInTheDocument();
    expect(within(failedCard).getByText(/^Versucht /)).toBeInTheDocument();
    expect(within(okCard).getByText(/^Geprüft /)).toBeInTheDocument();
    // Das gescheiterte Audit hat keine Warnungsliste, das erfolgreiche schon.
    expect(failedCard).not.toHaveTextContent("Warnungen (");
    expect(within(okCard).getByText(/SSH erlaubt Root-Login/)).toBeInTheDocument();
    expect(screen.getAllByText(/Letztes Audit fehlgeschlagen/)).toHaveLength(1);
  });

  it("unbekanntes Alter der Signaturen: Hinweis in der Server-Zeile, auch bei unlesbarem Datum, und ein ruhiger Eintrag unter „Braucht Aufmerksamkeit“", async () => {
    const overview = {
      ...OVERVIEW,
      hosts: [
        host("h-unreadable", { signature_date: "Wed Foo 24 08:23:12 2026", signature_stale: null, signature_age_days: null, freshclam_active: true }),
        host("h-nodate", { signature_date: null, signature_stale: null, signature_age_days: null, freshclam_active: true }),
        host("h-fresh", { signature_stale: false, signature_age_days: 1, freshclam_active: true }),
      ],
      summary: { ...OVERVIEW.summary, hosts: 3, stale_signatures: 0 },
      attention: [{
        kind: "signatures", tone: "info", host_id: "h-unreadable", host_name: "h-unreadable",
        title: "h-unreadable: Alter der Virensignaturen unbekannt",
        hint: "Das Datum der Signaturen fehlt oder ist nicht lesbar, vielleicht fehlen die Signaturen.", action_label: "Signaturen aktualisieren",
      }],
    };
    stubFetch([], overview);
    render(<SocPage />);
    const unreadable = within(await screen.findByTestId("host-h-unreadable"));
    expect(unreadable.getByText(/Alter unbekannt/)).toBeInTheDocument();
    expect(unreadable.getByText(/Wed Foo 24 08:23:12 2026/)).toBeInTheDocument();
    expect(within(screen.getByTestId("host-h-nodate")).getByText(/Alter unbekannt/)).toBeInTheDocument();
    expect(within(screen.getByTestId("host-h-fresh")).queryByText(/Alter unbekannt/)).toBeNull();

    const item = within(screen.getByTestId("shield-attention")).getByText("h-unreadable: Alter der Virensignaturen unbekannt").closest("li") as HTMLElement;
    const dot = item.querySelector("span[aria-hidden='true']") as HTMLElement;
    expect(dot.className).toContain("bg-white/40");
    expect(dot.className).not.toContain("bg-amber-300");
    expect(within(item).getByRole("button", { name: "Signaturen aktualisieren" })).toBeInTheDocument();
  });
});
