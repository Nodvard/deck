import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { UpdatesTab, type UpdatesOverview } from "./UpdatesTab";

const now = Date.now() / 1000;

const DATA: UpdatesOverview = {
  hosts: [
    {
      host_id: "h-pi", host_name: "Raspberry Pi", host_status: "up", checking: false, busy: null, last_run: null,
      status: {
        manager: "apt", count: 3, security_count: 2, reboot_required: true, reboot_reasons: ["neuer Kernel 6.8.12-10-pve (läuft: 6.8.12-4-pve)"],
        kernel: "6.8.12-4-pve", latest_kernel: "6.8.12-10-pve", uptime_s: 200000, unattended: false, refresh_error: null, error: null, checked_at: now - 120,
        packages: [
          { name: "openssl", new_version: "3.0.14", current_version: "3.0.13", repo: "stable-security", security: true },
          { name: "libssl3", new_version: "3.0.14", current_version: "3.0.13", repo: "stable-security", security: true },
          { name: "base-files", new_version: "12.4+deb12u7", current_version: "12.4+deb12u6", repo: "stable", security: false },
        ],
      },
    },
    {
      host_id: "h-docker", host_name: "docker", host_status: "up", checking: false, busy: null,
      status: {
        manager: "apt", count: 0, security_count: 0, reboot_required: false, reboot_reasons: [], kernel: "6.1.0-25-amd64", latest_kernel: null,
        uptime_s: 5000, unattended: true, refresh_error: null, error: null, checked_at: now - 60, packages: [],
      },
      last_run: {
        id: "r1", host_id: "h-docker", host_name: "docker", mode: "security", mode_label: "Sicherheitsupdates", trigger: "schedule",
        status: "ok", upgraded: 4, summary: "4 Paket(e) aktualisiert", output_tail: "4 upgraded", started_at: now - 3600, finished_at: now - 3500,
      },
    },
  ],
  summary: { hosts: 2, checked: 2, up_to_date: 1, packages: 3, security: 2, reboot: 1, errors: 0 },
  runs: [
    {
      id: "r1", host_id: "h-docker", host_name: "docker", mode: "security", mode_label: "Sicherheitsupdates", trigger: "schedule",
      status: "ok", upgraded: 4, summary: "4 Paket(e) aktualisiert", output_tail: "4 upgraded, 0 newly installed", started_at: now - 3600, finished_at: now - 3500,
    },
  ],
  config: { check_enabled: true, check_cron: "0 6 * * *", auto_enabled: true, auto_mode: "security", auto_cron: "30 3 * * *", auto_reboot: false, auto_tag: null },
};

type Call = { url: string; method: string; body: unknown };

function mockFetch(calls: Call[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/defender/updates")) return json(DATA);
    if (url.endsWith("/defender/updates/check")) return json({ hosts: 2 });
    if (url.endsWith("/hosts/h-pi/upgrade")) return json({ action_id: "a1", status: "proposed" });
    if (url.endsWith("/hosts/h-pi/install")) return json({ action_id: "a2", status: "proposed" });
    if (url.endsWith("/actions/a1/approve") || url.endsWith("/actions/a2/approve")) return json({ status: "succeeded", result: {} });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("Update-Zentrale", () => {
  it("ohne Server: Hinweis mit Link zu „Server & Zugänge“", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...DATA, hosts: [] }), { status: 200 })));
    render(<UpdatesTab canManage />);
    expect(await screen.findByText(/Linux-Server mit SSH-Zugang angelegt/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts");
  });

  it("zeigt Zusammenfassung, Zeitpläne und den Stand je Server", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<UpdatesTab canManage />);

    const pi = within(await screen.findByTestId("updates-h-pi"));
    expect(pi.getByText("2 Sicherheitsupdates")).toBeInTheDocument();
    expect(pi.getByText("Neustart nötig")).toBeInTheDocument();
    expect(pi.getByText(/neuer Kernel 6.8.12-10-pve/)).toBeInTheDocument();
    expect(within(screen.getByTestId("updates-h-docker")).getByText("aktuell")).toBeInTheDocument();
    expect(screen.getByText(/Prüfung: Täglich um 06:00/)).toBeInTheDocument();
    expect(screen.getByText(/Sicherheitsupdates · Täglich um 03:30/)).toBeInTheDocument();

    fireEvent.click(pi.getByText("3 Pakete anzeigen"));
    expect(pi.getByText("openssl")).toBeInTheDocument();
    expect(pi.getAllByText("Sicherheit")).toHaveLength(2);
  });

  it("ein Server ohne bekannten Paketmanager (z. B. NAS) steht als „nicht unterstützt“ da, nicht als Fehler", async () => {
    const nas = {
      host_id: "h-nas", host_name: "NAS", host_status: "unknown", checking: false, busy: null, last_run: null,
      status: {
        manager: null, count: 0, security_count: 0, reboot_required: false, reboot_reasons: [], kernel: null, latest_kernel: null,
        uptime_s: null, unattended: null, refresh_error: null, error: "Kein unterstützter Paketmanager (apt, dnf, apk) gefunden.",
        unsupported: true, checked_at: Date.now() / 1000 - 60, packages: [],
      },
    };
    const data = { ...DATA, hosts: [...DATA.hosts, nas], summary: { ...DATA.summary, hosts: 3 } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })));
    render(<UpdatesTab canManage />);

    const row = within(await screen.findByTestId("updates-h-nas"));
    expect(row.getByText("nicht unterstützt")).toBeInTheDocument();
    expect(row.queryByText("Fehler")).toBeNull();
    expect(screen.getByText("1 nicht unterstützt")).toBeInTheDocument();
    expect(screen.queryByText(/noch nicht geprüft/)).toBeNull();
  });

  it("spielt Sicherheitsupdates nach Rückfrage über das Gate ein", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<UpdatesTab canManage />);
    const pi = within(await screen.findByTestId("updates-h-pi"));

    fireEvent.click(pi.getByRole("button", { name: "Sicherheitsupdates einspielen" }));
    await waitFor(() => expect(screen.getByText("Raspberry Pi: Erledigt.")).toBeInTheDocument());
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("2 Sicherheitsupdate(s)"), expect.anything());
    expect(calls.find((c) => c.url.endsWith("/hosts/h-pi/upgrade"))?.body).toEqual({ mode: "security" });
    expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true);
  });

  it("fragt eine lange Aktion (202 'executing') nach, statt sie als Fehler zu zeigen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const calls: Call[] = [];
      const base = mockFetch(calls);
      let polls = 0;
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/actions/a1/approve")) {
          calls.push({ url, method: "POST", body: null });
          return new Response(JSON.stringify({ status: "executing", result: null }), { status: 202 });
        }
        if (url.endsWith("/actions/a1")) {
          polls += 1;
          const status = polls < 2 ? "executing" : "succeeded";
          return new Response(JSON.stringify({ id: "a1", status, result: {} }), { status: 200 });
        }
        return base(input, init);
      }));
      render(<UpdatesTab canManage />);
      const pi = within(await screen.findByTestId("updates-h-pi"));

      fireEvent.click(pi.getByRole("button", { name: "Sicherheitsupdates einspielen" }));
      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true));
      // Noch nicht fertig: weder Ergebnis noch Fehler, der Hinweis "läuft" bleibt stehen.
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(polls).toBe(1);
      expect(screen.queryByText(/Fehlgeschlagen/)).not.toBeInTheDocument();
      expect(screen.queryByText("Raspberry Pi: Erledigt.")).not.toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      await waitFor(() => expect(screen.getByText("Raspberry Pi: Erledigt.")).toBeInTheDocument());
    } finally {
      vi.useRealTimers();
    }
  });

  it("hört nach dem Verlassen des Reiters auf, eine laufende Aktion nachzufragen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const calls: Call[] = [];
      const base = mockFetch(calls);
      let polls = 0;
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/actions/a1/approve")) {
          calls.push({ url, method: "POST", body: null });
          return new Response(JSON.stringify({ status: "executing", result: null }), { status: 202 });
        }
        if (url.endsWith("/actions/a1")) {
          polls += 1;
          return new Response(JSON.stringify({ id: "a1", status: "executing", result: {} }), { status: 200 });
        }
        return base(input, init);
      }));
      const view = render(<UpdatesTab canManage />);
      const pi = within(await screen.findByTestId("updates-h-pi"));
      fireEvent.click(pi.getByRole("button", { name: "Sicherheitsupdates einspielen" }));
      await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(polls).toBe(1);
      view.unmount();
      await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
      expect(polls).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  describe("Proxmox: „Alle Updates“ als dist-upgrade", () => {
    const withOption = (on: boolean): UpdatesOverview => ({ ...DATA, config: { ...DATA.config, proxmox_dist_upgrade: on } });

    function stubWith(data: UpdatesOverview, calls: Call[], upgradeAnswer: unknown) {
      const base = mockFetch(calls);
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/defender/updates")) return new Response(JSON.stringify(data), { status: 200 });
        if (url.endsWith("/hosts/h-pi/upgrade")) {
          calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(init.body as string) : null });
          return new Response(JSON.stringify(upgradeAnswer), { status: 200 });
        }
        return base(input, init);
      }));
    }

    it("mit der Einstellung nennt die Rückfrage dist-upgrade und dass Pakete entfernt werden können", async () => {
      stubWith(withOption(true), [], { action_id: "a1", status: "proposed", risk: "high" });
      render(<UpdatesTab canManage />);
      fireEvent.click(within(await screen.findByTestId("updates-h-pi")).getByRole("button", { name: "Alle einspielen" }));

      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      const [text, options] = confirmDialog.mock.calls[0] as unknown as [string, { danger?: boolean }];
      expect(text).toContain("dist-upgrade");
      expect(text).toContain("Pakete entfernen");
      expect(options.danger).toBe(true);
    });

    it("ohne die Einstellung bleibt die Rückfrage wie bisher", async () => {
      stubWith(withOption(false), [], { action_id: "a1", status: "proposed", risk: "medium" });
      render(<UpdatesTab canManage />);
      fireEvent.click(within(await screen.findByTestId("updates-h-pi")).getByRole("button", { name: "Alle einspielen" }));

      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      const [text, options] = confirmDialog.mock.calls[0] as unknown as [string, { danger?: boolean }];
      expect(text).not.toContain("dist-upgrade");
      expect(options.danger).toBe(false);
    });

    it("die Stufe aus der Antwort der Route zählt: ohne Recht für „hoch“ wird nicht freigegeben, der Vorschlag bleibt offen", async () => {
      const calls: Call[] = [];
      stubWith(withOption(true), calls, { action_id: "a1", status: "proposed", risk: "high" });
      window.__lattice.hasPermission = vi.fn((p: string) => p !== "actions.approve:high");
      render(<UpdatesTab canManage />);
      fireEvent.click(within(await screen.findByTestId("updates-h-pi")).getByRole("button", { name: "Alle einspielen" }));

      await waitFor(() => expect(screen.getByText(/Wartet auf Freigabe/)).toBeInTheDocument());
      expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
      expect(screen.queryByText(/Fehlgeschlagen/)).not.toBeInTheDocument();
    });
  });

  it("Neustart fragt deutlich nach und braucht die Freigabe für hohes Risiko", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    const hasPermission = vi.fn((p: string) => p !== "actions.approve:high");
    window.__lattice.hasPermission = hasPermission;
    render(<UpdatesTab canManage />);
    const pi = within(await screen.findByTestId("updates-h-pi"));

    fireEvent.click(pi.getByRole("button", { name: "Neu starten" }));
    await waitFor(() => expect(screen.getByText(/Wartet auf Freigabe/)).toBeInTheDocument());
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("nicht erreichbar"), expect.objectContaining({ danger: true }));
    expect(calls.find((c) => c.url.endsWith("/hosts/h-pi/upgrade"))?.body).toEqual({ mode: "reboot" });
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
  });

  it("zeigt nach ausgelöstem Neustart weder „Neustart nötig“ noch den Knopf erneut", async () => {
    const since = now - 30;
    const pi = DATA.hosts[0];
    const data: UpdatesOverview = {
      ...DATA,
      hosts: [{ ...pi, status: { ...pi.status!, checked_at: now - 3600, reboot_pending_since: since } }, DATA.hosts[1]],
      summary: { ...DATA.summary, reboot: 0 },
    };
    const fallback = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      return url.endsWith("/defender/updates") ? new Response(JSON.stringify(data), { status: 200 }) : fallback(input, init);
    }));
    render(<UpdatesTab canManage />);

    const row = within(await screen.findByTestId("updates-h-pi"));
    const hhmm = new Date(since * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    expect(row.getByText(`Neustart ausgelöst (${hhmm}) – die nächste Prüfung zeigt den neuen Stand.`)).toBeInTheDocument();
    expect(row.queryByText("Neustart nötig")).toBeNull();
    expect(row.queryByRole("button", { name: "Neu starten" })).toBeNull();
    expect(row.queryByText(/Grund:/)).toBeNull();
    // Einspielen und Prüfen bleiben möglich.
    expect(row.getByRole("button", { name: "Sicherheitsupdates einspielen" })).toBeInTheDocument();
    expect(row.getByRole("button", { name: "Raspberry Pi prüfen" })).toBeInTheDocument();
  });

  it("prüft alle Server und zeigt den Verlauf mit Ausgabe", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<UpdatesTab canManage />);
    await screen.findByTestId("updates-h-pi");

    fireEvent.click(screen.getByRole("button", { name: /Alle jetzt prüfen/ }));
    await waitFor(() => expect(screen.getByText("2 Server werden geprüft …")).toBeInTheDocument());
    expect(calls.find((c) => c.url.endsWith("/updates/check"))?.body).toEqual({ host_ids: "all" });

    const runs = within(screen.getByTestId("update-runs"));
    expect(runs.getByText("automatisch")).toBeInTheDocument();
    fireEvent.click(runs.getByText("Ausgabe"));
    expect(runs.getByText("4 upgraded, 0 newly installed")).toBeInTheDocument();
  });

  it("ohne Verwaltungsrecht keine Knöpfe", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<UpdatesTab canManage={false} />);
    await screen.findByTestId("updates-h-pi");
    expect(screen.queryByRole("button", { name: "Sicherheitsupdates einspielen" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Alle jetzt prüfen/ })).toBeNull();
  });
});
