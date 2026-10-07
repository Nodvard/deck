import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { HostFilter } from "./api";
import { UpdatesTab, type ProposeAllResult, type UpdatesOverview } from "./UpdatesTab";

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
        // So steht es im Backend: „kein Paketmanager“ ist ein gültiges Ergebnis, die Prüfung ist erfolgt.
        unsupported: true, checked_at: now - 60, attempted_at: now - 60, packages: [],
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
    expect(row.queryByText(/noch nie erfolgreich geprüft/)).toBeNull();
    expect(row.getByText(/geprüft gerade eben/)).toBeInTheDocument();
  });

  it("scheitert die Prüfung, steht der Grund da, dazu die letzte erfolgreiche Prüfung und der letzte Versuch; ausgebliebene Prüfung wird gemeldet", async () => {
    const base = DATA.hosts[1];
    const failing = {
      ...base, host_id: "h-fail", host_name: "pve2", check_overdue: false,
      status: { ...base.status!, error: "Verbindung abgelehnt", checked_at: now - 3 * 86400, attempted_at: now - 600 },
    };
    const never = {
      ...base, host_id: "h-never", host_name: "neu", check_overdue: false,
      status: { ...base.status!, manager: null, error: "Zeitüberschreitung", checked_at: null, attempted_at: now - 600 },
    };
    const missed = { ...base, host_id: "h-missed", host_name: "alt", check_overdue: true, status: { ...base.status!, checked_at: now - 3 * 86400 } };
    const data = { ...DATA, hosts: [DATA.hosts[0], failing, never, missed] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })));
    render(<UpdatesTab canManage />);

    const fail = (await screen.findByTestId("updates-h-fail")).textContent ?? "";
    expect(fail).toContain("Verbindung abgelehnt");
    expect(fail).toContain("geprüft vor 3 Tagen");
    expect(fail).toContain("letzter Versuch vor 10 Min. fehlgeschlagen");
    const nie = screen.getByTestId("updates-h-never").textContent ?? "";
    expect(nie).toContain("noch nie erfolgreich geprüft");
    expect(nie).toContain("Zeitüberschreitung");
    expect(screen.getByTestId("updates-overdue-h-missed").textContent).toContain("geplante Prüfung ist hier ausgeblieben");
    expect(screen.queryByTestId("updates-overdue-h-pi")).toBeNull();
    expect(screen.queryByTestId("updates-overdue-h-fail")).toBeNull();
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

  describe("Alle Updates vorschlagen", () => {
    const base = DATA.hosts[0];
    const baseState = base.status!;
    const host = (id: string, name: string, patch: Partial<NonNullable<typeof base.status>> | null, extra: object = {}) => ({
      ...base, host_id: id, host_name: name, last_run: null, ...extra,
      status: patch === null ? null : { ...baseState, reboot_required: false, reboot_reasons: [], ...patch },
    });
    // pi (2 Sicherheitsupdates, 3 gesamt), web (1 Sicherheitsupdate, 4 gesamt), db (nur normale Updates), docker (aktuell),
    // nas (nicht unterstützt), neu (nie geprüft), busy (spielt gerade ein).
    const MANY: UpdatesOverview = {
      ...DATA,
      hosts: [
        DATA.hosts[0],
        host("h-web", "web", { count: 4, security_count: 1 }),
        host("h-db", "db", { count: 2, security_count: 0 }),
        DATA.hosts[1],
        host("h-nas", "NAS", { manager: null, count: 0, security_count: 0, unsupported: true, error: "Kein Paketmanager" }),
        host("h-neu", "neu", null),
        host("h-busy", "beschäftigt", { count: 9, security_count: 3 }, { busy: "all" }),
      ],
    };
    const ANSWER: ProposeAllResult = {
      mode: "security",
      results: [
        { host_id: "h-pi", name: "Raspberry Pi", result: "proposed", action_id: "a1", status: "proposed", risk: "medium" },
        { host_id: "h-web", name: "web", result: "proposed", action_id: "a2", status: "proposed", risk: "medium" },
      ],
      counts: { proposed: 2, skipped: 0, failed: 0, total: 2 },
    };

    function stub(data: UpdatesOverview, calls: Call[], answer: ProposeAllResult | { status: number; body: unknown } = ANSWER) {
      const fallback = mockFetch(calls);
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/defender/updates")) return new Response(JSON.stringify(data), { status: 200 });
        if (url.endsWith("/defender/updates/propose-all")) {
          calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(init.body as string) : null });
          return "status" in answer && "body" in answer
            ? new Response(JSON.stringify(answer.body), { status: answer.status })
            : new Response(JSON.stringify(answer), { status: 200 });
        }
        return fallback(input, init);
      }));
    }

    it("zeigt je Art einen Knopf mit der Zahl der Server, für die etwas anzulegen wäre", async () => {
      stub(MANY, []);
      render(<UpdatesTab canManage />);
      // Sicherheitsupdates: pi, web. Alle Updates: pi, web, db. Nicht dabei: aktuell, nicht unterstützt, nie geprüft, beschäftigt.
      expect(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" })).toBeInTheDocument();
    });

    it("fehlt, wenn nichts offen ist, ohne Verwaltungsrecht und in der Ansicht eines einzelnen Servers", async () => {
      const upToDate: UpdatesOverview = { ...DATA, hosts: [DATA.hosts[1]] };
      stub(upToDate, []);
      const view = render(<UpdatesTab canManage />);
      await screen.findByTestId("updates-h-docker");
      expect(screen.queryByRole("button", { name: /vorschlagen/ })).toBeNull();
      view.unmount();

      stub(MANY, []);
      const readOnly = render(<UpdatesTab canManage={false} />);
      await screen.findByTestId("updates-h-pi");
      expect(screen.queryByRole("button", { name: /vorschlagen/ })).toBeNull();
      readOnly.unmount();

      render(<HostFilter.Provider value="h-pi"><UpdatesTab canManage /></HostFilter.Provider>);
      await screen.findByTestId("updates-h-pi");
      expect(screen.queryByRole("button", { name: /vorschlagen/ })).toBeNull();
      expect(screen.getByRole("button", { name: "Sicherheitsupdates einspielen" })).toBeInTheDocument(); // die Einzel-Knöpfe bleiben
    });

    it("sind nur normale Updates offen, gibt es nur „Alle Updates vorschlagen“", async () => {
      const onlyNormal: UpdatesOverview = { ...DATA, hosts: [host("h-db", "db", { count: 2, security_count: 0 })] };
      stub(onlyNormal, []);
      render(<UpdatesTab canManage />);
      expect(await screen.findByRole("button", { name: "Alle Updates vorschlagen (1)" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Alle Sicherheitsupdates vorschlagen/ })).toBeNull();
    });

    it("fragt mit der Zahl und den Namen nach; bei „Abbrechen“ wird nichts angelegt", async () => {
      const calls: Call[] = [];
      stub(MANY, calls);
      confirmDialog.mockResolvedValue(false);
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));

      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      const [text, options] = confirmDialog.mock.calls[0] as unknown as [string, { confirmLabel?: string; danger?: boolean }];
      expect(text).toContain("die Sicherheitsupdates auf 2 Servern");
      expect(text).toContain("Raspberry Pi, web");
      // Ehrlich: erst nach der Freigabe – es sei denn, die Automatik steht auf „Selbstständig handeln“ (dann startet es sofort).
      expect(text).toContain("Eingespielt wird erst nach deiner Freigabe unter „Aktionen“");
      expect(text).toContain("Einstellungen → Automatik");
      expect(text).toContain("„Selbstständig handeln“");
      expect(text).not.toContain("noch nichts eingespielt");
      expect(text).toContain("Dort kannst du sie auch gesammelt freigeben.");
      expect(text).not.toContain("einzeln");
      expect(text).not.toContain("beschäftigt");
      expect(options).toMatchObject({ confirmLabel: "Vorschläge anlegen", danger: false });
      expect(calls.some((c) => c.url.endsWith("/propose-all"))).toBe(false);
      expect(screen.queryByTestId("propose-all-result")).toBeNull();
    });

    it("legt die Vorschläge für genau die bestätigten Server an, gibt nichts frei und zeigt Zusammenfassung mit Link zu „Aktionen“", async () => {
      const calls: Call[] = [];
      stub(MANY, calls, {
        mode: "security",
        results: [
          { host_id: "h-pi", name: "Raspberry Pi", result: "proposed", action_id: "a1", status: "proposed", risk: "medium" },
          { host_id: "h-web", name: "web", result: "skipped", reason: "Vorschlag „Alle Updates“ wartet schon auf Freigabe." },
        ],
        counts: { proposed: 1, skipped: 1, failed: 0, total: 2 },
      });
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));

      const summary = within(await screen.findByTestId("propose-all-result"));
      expect(summary.getByText("1 vorgeschlagen, 1 übersprungen.")).toBeInTheDocument();
      expect(summary.getByText("Übersprungen: web (Vorschlag „Alle Updates“ wartet schon auf Freigabe).")).toBeInTheDocument();
      expect(summary.getByRole("link", { name: "Zu „Aktionen“ zum Freigeben" })).toHaveAttribute("href", "/actions");
      expect(calls.find((c) => c.url.endsWith("/propose-all"))).toMatchObject({ method: "POST", body: { mode: "security", host_ids: ["h-pi", "h-web"] } });
      // Nur vorschlagen: kein Weg über die Einzelroute und keine Freigabe.
      expect(calls.some((c) => c.url.includes("/hosts/") || c.url.includes("/approve"))).toBe(false);
      expect(screen.queryByText(/Fehlgeschlagen/)).toBeNull();
    });

    it("„Alle Updates“ schickt dieselben Server wie die Rückfrage und nennt bei der Proxmox-Einstellung dist-upgrade", async () => {
      const calls: Call[] = [];
      stub({ ...MANY, config: { ...MANY.config, proxmox_dist_upgrade: true } }, calls, {
        ...ANSWER, mode: "all", results: ANSWER.results.map((r) => ({ ...r, risk: "high" })),
      });
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Updates vorschlagen (3)" }));

      await screen.findByTestId("propose-all-result");
      const [text, options] = confirmDialog.mock.calls[0] as unknown as [string, { danger?: boolean }];
      expect(text).toContain("alle Updates auf 3 Servern");
      expect(text).toContain("dist-upgrade");
      // Hohes Risiko steht auf der Aktionen-Seite nicht in der Sammel-Freigabe: die Rückfrage verspricht sie nicht.
      expect(text).toContain("Du gibst sie einzeln frei, sie stehen nicht in der Sammel-Freigabe.");
      expect(text).not.toContain("auch gesammelt freigeben");
      expect(options.danger).toBe(true);
      expect(calls.find((c) => c.url.endsWith("/propose-all"))?.body).toEqual({ mode: "all", host_ids: ["h-pi", "h-web", "h-db"] });
    });

    it("ohne die Proxmox-Einstellung (und bei Sicherheitsupdates) kein Hinweis auf dist-upgrade", async () => {
      stub({ ...MANY, config: { ...MANY.config, proxmox_dist_upgrade: false } }, []);
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Updates vorschlagen (3)" }));
      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      expect(String(confirmDialog.mock.calls[0][0])).not.toContain("dist-upgrade");
      expect(String(confirmDialog.mock.calls[0][0])).toContain("Dort kannst du sie auch gesammelt freigeben.");
      expect(String(confirmDialog.mock.calls[0][0])).not.toContain("einzeln");
      expect((confirmDialog.mock.calls[0] as unknown as [string, { danger?: boolean }])[1].danger).toBe(false);
    });

    it("gemischt (apt mit dist-upgrade, dnf ohne): die Rückfrage nennt, welche einzeln freigegeben werden und welche gesammelt", async () => {
      const mixed: UpdatesOverview = {
        ...MANY,
        config: { ...MANY.config, proxmox_dist_upgrade: true },
        hosts: [...MANY.hosts, host("h-fed", "fedora", { manager: "dnf", count: 5, security_count: 0 })],
      };
      stub(mixed, []);
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Updates vorschlagen (4)" }));
      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      const text = String(confirmDialog.mock.calls[0][0]);
      expect(text).toContain("haben die Vorschläge für Raspberry Pi, web, db hohes Risiko: Die gibst du einzeln frei, die übrigen auch gesammelt.");
      expect(text).not.toContain("fedora hohes Risiko");
      expect(text).not.toContain("sie stehen nicht in der Sammel-Freigabe");
    });

    it("bei Sicherheitsupdates gilt dist-upgrade nie: auch mit der Proxmox-Einstellung wird gesammelt freigegeben", async () => {
      stub({ ...MANY, config: { ...MANY.config, proxmox_dist_upgrade: true } }, []);
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));
      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      expect(String(confirmDialog.mock.calls[0][0])).toContain("Dort kannst du sie auch gesammelt freigeben.");
      expect(String(confirmDialog.mock.calls[0][0])).not.toContain("einzeln");
    });

    describe("Zusammenfassung: gesammelt oder einzeln freigeben", () => {
      const open = (risk: string, ids: string[]): ProposeAllResult => ({
        mode: "all",
        results: ids.map((id, i) => ({ host_id: id, name: id, result: "proposed" as const, action_id: `a${i}`, status: "proposed", risk })),
        counts: { proposed: ids.length, skipped: 0, failed: 0, total: ids.length },
      });

      async function summaryFor(answer: ProposeAllResult) {
        stub(MANY, [], answer);
        render(<UpdatesTab canManage />);
        fireEvent.click(await screen.findByRole("button", { name: "Alle Updates vorschlagen (3)" }));
        return (await screen.findByTestId("propose-all-result")).textContent ?? "";
      }

      it("normales Risiko: dort geht es auch gesammelt", async () => {
        const text = await summaryFor(open("medium", ["h-pi", "h-web"]));
        expect(text).toContain("Zu „Aktionen“ zum Freigeben – dort geht es auch gesammelt.");
        expect(text).not.toContain("einzeln");
      });

      it("hohes Risiko (dist-upgrade): nur einzeln, nicht gesammelt", async () => {
        const text = await summaryFor(open("high", ["h-pi", "h-web"]));
        expect(text).toContain("diese 2 Vorschläge haben hohes Risiko und werden einzeln freigegeben, nicht gesammelt.");
        expect(text).not.toContain("dort geht es auch gesammelt");
      });

      it("kritisches Risiko, ein Vorschlag: ebenfalls nur einzeln", async () => {
        const text = await summaryFor(open("critical", ["h-pi"]));
        expect(text).toContain("dieser Vorschlag hat hohes Risiko und wird einzeln freigegeben, nicht gesammelt.");
      });

      it("gemischt: sagt beides", async () => {
        const answer = open("medium", ["h-pi", "h-web", "h-db"]);
        answer.results[0].risk = "high";
        const text = await summaryFor(answer);
        expect(text).toContain("2 Vorschläge kannst du gesammelt freigeben, 1 mit hohem Risiko nur einzeln.");
        expect(text).not.toContain("dort geht es auch gesammelt");
      });

      it("schon von der Automatik gestartete Vorschläge zählen nicht als „einzeln freizugeben“", async () => {
        const answer = open("high", ["h-pi", "h-web"]);
        answer.results[0].status = "executing";
        const text = await summaryFor(answer);
        expect(text).toContain("Bei 1 Server hat die Automatik schon freigegeben");
        expect(text).toContain("dieser Vorschlag hat hohes Risiko und wird einzeln freigegeben, nicht gesammelt.");
      });
    });

    it("Fehlschläge stehen mit Grund da; ohne neuen Vorschlag gibt es keinen Link zum Freigeben", async () => {
      stub(MANY, [], {
        mode: "security",
        results: [
          { host_id: "h-pi", name: "Raspberry Pi", result: "failed", reason: "Keine Sicherheitsupdates offen." },
          { host_id: "h-web", name: "web", result: "skipped", reason: "Es läuft gerade ein Einspiel-Lauf." },
        ],
        counts: { proposed: 0, skipped: 1, failed: 1, total: 2 },
      });
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));

      const summary = within(await screen.findByTestId("propose-all-result"));
      expect(summary.getByText("Es wurde kein Vorschlag angelegt.")).toBeInTheDocument();
      expect(summary.getByText("Fehlgeschlagen: Raspberry Pi (Keine Sicherheitsupdates offen).")).toBeInTheDocument();
      expect(summary.getByText("Übersprungen: web (Es läuft gerade ein Einspiel-Lauf).")).toBeInTheDocument();
      expect(summary.queryByRole("link")).toBeNull();
    });

    it("hat die Automatik schon freigegeben, steht das da und der Link nur für die, die noch warten", async () => {
      stub(MANY, [], {
        mode: "security",
        results: [
          { host_id: "h-pi", name: "Raspberry Pi", result: "proposed", action_id: "a1", status: "executing", risk: "medium" },
          { host_id: "h-web", name: "web", result: "proposed", action_id: "a2", status: "executing", risk: "medium" },
        ],
        counts: { proposed: 2, skipped: 0, failed: 0, total: 2 },
      });
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));

      const summary = within(await screen.findByTestId("propose-all-result"));
      expect(summary.getByText("2 vorgeschlagen.")).toBeInTheDocument();
      expect(summary.getByText(/Bei 2 Servern hat die Automatik schon freigegeben/)).toBeInTheDocument();
      expect(summary.queryByRole("link")).toBeNull();
    });

    it("zeigt den Fehler der Route (z. B. „gerade schon Vorschläge“) und lässt den Knopf wieder zu", async () => {
      stub(MANY, [], { status: 409, body: { detail: "Es werden gerade schon Vorschläge angelegt – bitte einen Moment warten." } });
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));

      expect(await screen.findByText(/gerade schon Vorschläge angelegt/)).toBeInTheDocument();
      expect(screen.queryByTestId("propose-all-result")).toBeNull();
      expect(screen.getByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" })).toBeEnabled();
    });

    it("sperrt beide Knöpfe, solange die Vorschläge angelegt werden", async () => {
      let release: (r: Response) => void = () => undefined;
      const fallback = mockFetch([]);
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/defender/updates")) return new Response(JSON.stringify(MANY), { status: 200 });
        if (url.endsWith("/propose-all")) return new Promise<Response>((resolve) => { release = resolve; });
        return fallback(input, init);
      }));
      render(<UpdatesTab canManage />);
      fireEvent.click(await screen.findByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" }));

      expect(await screen.findByRole("button", { name: "Lege Vorschläge an …" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" })).toBeDisabled();
      await act(async () => { release(new Response(JSON.stringify(ANSWER), { status: 200 })); });
      expect(await screen.findByTestId("propose-all-result")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Alle Sicherheitsupdates vorschlagen (2)" })).toBeEnabled();
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
