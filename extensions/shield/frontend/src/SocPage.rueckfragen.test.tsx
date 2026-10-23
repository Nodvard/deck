import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { SocPage } from "./SocPage";

const HOST = { host_id: "h1", host_name: "pi", host_status: "up", reachable: true, clamav_installed: true, lynis_installed: true, last_scan: null, last_audit: null, scanning: false, auditing: false };
const OVERVIEW = {
  hosts: [HOST, { ...HOST, host_id: "h2", host_name: "mini" }],
  summary: { hosts: 2, protected: 2, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: null, score: 80 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};
const FINDINGS = [
  { id: "f1", host_id: "h1", host_name: "pi", path: "/tmp/eicar.com", signature: "Win.Test.EICAR_HC-1", status: "detected", quarantine_path: null, note: null, detected_at: 1758800000, status_changed_at: null },
  { id: "f2", host_id: "h1", host_name: "pi", path: "/srv/tool.sh", signature: "Unix.Tool", status: "ignored", quarantine_path: null, note: null, detected_at: 1758700000, status_changed_at: 1758700001 },
];

type Call = { url: string; method: string; body: unknown };

function mockFetch(calls: Call[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) return json(OVERVIEW);
    if (url.includes("/defender/findings") && method === "GET") return json(FINDINGS);
    if ((url.includes("/defender/scans") || url.includes("/defender/audits")) && method === "GET") return json([]);
    if (url.includes("/defender/scans") && method === "POST") return json({ scans: ["s1"] });
    if (url.includes("/defender/audits") && method === "POST") return json({ hosts: 1, started: ["h1"], running: [], audits: [] });
    if (url.includes("/defender/findings/") && method === "POST") return json({ ok: true });
    if (url.includes("/watch-templates")) return json({ templates: [] });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string, opts?: unknown) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string, opts?: unknown) => Promise<boolean>>().mockResolvedValue(false);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

function open(search: string) {
  window.history.replaceState(null, "", `/ext/shield/soc?${search}`);
}

const posts = (calls: Call[], part: string) => calls.filter((c) => c.method === "POST" && c.url.includes(part));

describe("Rückfrage vor Arbeit auf allen Servern", () => {
  it("Tiefenscan auf allen Servern über das Formular fragt erst", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    open("tab=scans");
    render(<SocPage />);
    fireEvent.change(await screen.findByLabelText("Scan-Art"), { target: { value: "deep" } });
    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Tiefenscan auf allen 2 Servern"), expect.anything()));
    expect(posts(calls, "/defender/scans")).toEqual([]);
  });

  it("Tiefenscan auf einem gewählten Server startet ohne Rückfrage", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    open("tab=scans");
    render(<SocPage />);
    fireEvent.change(await screen.findByLabelText("Scan-Art"), { target: { value: "deep" } });
    fireEvent.change(screen.getByLabelText("Scan-Ziel"), { target: { value: "h2" } });
    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    await screen.findByText("1 Scan gestartet.");
    expect(confirmDialog).not.toHaveBeenCalled();
    expect(posts(calls, "/defender/scans")[0].body).toMatchObject({ kind: "deep", host_ids: ["h2"] });
  });

  it("„Audit jetzt starten“ ohne bisheriges Audit fragt erst", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    open("tab=hardening");
    render(<SocPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Audit jetzt starten" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Härtungs-Audit auf allen 2 Servern"), expect.anything()));
    expect(posts(calls, "/defender/audits")).toEqual([]);
  });

  it("in der Ansicht eines Servers prüft „Audit jetzt starten“ nur diesen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    open("tab=hardening&host=h1");
    render(<SocPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Audit jetzt starten" }));
    await waitFor(() => expect(posts(calls, "/defender/audits")).toHaveLength(1));
    expect(posts(calls, "/defender/audits")[0].body).toEqual({ host_ids: ["h1"] });
    expect(confirmDialog).not.toHaveBeenCalled();
  });
});

describe("Ignorieren und der Weg zurück", () => {
  it("die Rückfrage sagt, dass dieselbe Datei danach nicht mehr gemeldet wird", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    open("tab=quarantine");
    render(<SocPage />);
    const row = (await screen.findByText(/\/tmp\/eicar\.com/)).closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Ignorieren" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("meldet Shield sie nicht mehr"), expect.anything()));
    expect(confirmDialog.mock.calls[0][0]).toContain("„Wieder beachten“");
  });

  it("ein ignorierter Fund lässt sich wieder beachten", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    confirmDialog.mockResolvedValue(true);
    open("tab=quarantine");
    render(<SocPage />);
    await screen.findByText(/\/tmp\/eicar\.com/);
    expect(screen.queryByText(/\/srv\/tool\.sh/)).toBeNull();
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "all" } });
    const row = screen.getByText(/\/srv\/tool\.sh/).closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Wieder beachten" }));
    await waitFor(() => expect(posts(calls, "/findings/f2/reopen")).toHaveLength(1));
    expect(confirmDialog.mock.calls[0][0]).toContain("Der nächste Scan meldet die Datei wieder und verschiebt sie");
    expect(await screen.findByText("„/srv/tool.sh“: Der Fund ist wieder offen. Der nächste Scan meldet die Datei wieder.")).toBeInTheDocument();
  });
});
