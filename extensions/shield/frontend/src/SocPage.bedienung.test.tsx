import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { SocPage, findingsLast30Days } from "./SocPage";

/** Shield-Bedienung: Knöpfe oben nur, wo sie hingehören, eine Zahl je Kachel. */

const OVERVIEW = {
  hosts: [
    { host_id: "h1", host_name: "pi", host_status: "up", reachable: true, clamav_installed: true, lynis_installed: true, last_scan: null, last_audit: null, scanning: false, auditing: false },
  ],
  summary: { hosts: 1, protected: 1, open_threats: 0, quarantined: 2, neutralized_total: 5, findings_30d: 3, avg_hardening: null, score: 80 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) return json(OVERVIEW);
    if (url.endsWith("/defender/updates")) {
      return json({
        hosts: [], runs: [], summary: { hosts: 1, checked: 1, up_to_date: 1, packages: 0, security: 0, reboot: 0 },
        config: { check_enabled: true, check_cron: "0 6 * * *", auto_enabled: false, auto_mode: "security", auto_cron: "30 3 * * *", auto_reboot: false, auto_tag: null },
      });
    }
    if (url.endsWith("/defender/guard")) return json({ hosts: [], events: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 1, hosts: 1 } });
    if ((url.includes("/defender/findings") || url.includes("/defender/scans") || url.includes("/defender/audits") || url.includes("/defender/events")) && method === "GET") return json([]);
    if (url.includes("/defender/scans") && method === "POST") return json({ scans: ["s1"] });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(false);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

function openTab(tab: string) {
  window.history.replaceState(null, "", `/ext/shield/soc?tab=${tab}`);
}

describe("Knöpfe oben", () => {
  it.each(["overview", "scans"])("auf „%s“ stehen die vier Knöpfe direkt da", async (tab) => {
    openTab(tab);
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    expect(await screen.findByRole("button", { name: /Schnellscan starten/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tiefenscan/ })).toBeInTheDocument();
    expect(screen.queryByTestId("shield-more")).toBeNull();
  });

  it.each(["updates", "guard", "quarantine", "hardening", "containers"])("auf „%s“ liegen sie unter „Mehr“ und funktionieren dort", async (tab) => {
    openTab(tab);
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    const more = await screen.findByTestId("shield-more");
    expect(more).not.toHaveAttribute("open");
    // Nicht mehr als eigene Knöpfe in der Kopfzeile: nur im Menü.
    expect(within(more).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual([
      "Briefing senden", "Härtungs-Audit", "Tiefenscan", "Schnellscan starten",
    ]);
    fireEvent.click(within(more).getByText("Mehr"));
    expect(more).toHaveAttribute("open");
    fireEvent.click(within(more).getByRole("button", { name: /Tiefenscan/ }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Tiefenscan"), expect.anything()));
    expect(more).not.toHaveAttribute("open");
    expect(document.activeElement?.textContent).toContain("Mehr"); // Fokus zurück auf „Mehr“, nicht auf die Seite
  });

  it("„Mehr“ schließt mit Escape und bei einem Klick daneben", async () => {
    openTab("updates");
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    const more = await screen.findByTestId("shield-more");
    // Das Ereignis „toggle“ kommt (wie im Browser) erst im nächsten Takt; erst dann hört das Menü auf Klicks daneben.
    const reopen = async () => {
      fireEvent.click(within(more).getByText("Mehr"));
      expect(more).toHaveAttribute("open");
      await act(() => new Promise((resolve) => setTimeout(resolve, 10)));
    };
    await reopen();
    fireEvent.keyDown(within(more).getByRole("button", { name: /Tiefenscan/ }), { key: "Escape" });
    expect(more).not.toHaveAttribute("open");

    await reopen();
    fireEvent.pointerDown(document.body); // Antippen am Handy (iOS schickt dabei keine Mausereignisse)
    expect(more).not.toHaveAttribute("open");

    await reopen();
    fireEvent.keyDown(document.body, { key: "Escape" }); // Escape auch ohne Fokus im Menü
    expect(more).not.toHaveAttribute("open");
    expect(document.activeElement?.tagName).toBe("SUMMARY");
  });

  it("ohne Verwaltungsrecht weder Knöpfe noch „Mehr“", async () => {
    window.__lattice.hasPermission = vi.fn().mockReturnValue(false);
    openTab("updates");
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    await screen.findByRole("tab", { name: /Updates/ });
    expect(screen.queryByTestId("shield-more")).toBeNull();
    expect(screen.queryByRole("button", { name: /Schnellscan starten/ })).toBeNull();
  });
});

describe("Kachel „In Quarantäne“", () => {
  it("zeigt eine Zahl statt einer Summe aus zwei verschiedenen Dingen", async () => {
    openTab("overview");
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    const tile = (await screen.findByText("In Quarantäne")).closest("div.panel") as HTMLElement;
    // Genau diese Kachel: Wert 2 (in Quarantäne), nicht 5 (Quarantäne plus Gelöschtes), darunter die Funde der 30 Tage.
    expect(tile).toHaveTextContent(/^In Quarantäne23 Funde in 30 Tagen$/);
    expect(screen.queryByText("Neutralisiert")).toBeNull();
  });

  it("Einzahl und keine Funde", () => {
    expect(findingsLast30Days(1)).toBe("1 Fund in 30 Tagen");
    expect(findingsLast30Days(0)).toBe("keine Funde in 30 Tagen");
  });
});

describe("Ergebnis einer Aktion", () => {
  const MINI = { ...OVERVIEW.hosts[0], host_id: "h-mini", host_name: "mini", clamav_installed: false };
  const NO_CLAM = { ...OVERVIEW, hosts: [MINI], summary: { ...OVERVIEW.summary, protected: 0 } };
  const FINDINGS = [
    { id: "f1", host_id: "h-mini", host_name: "mini", path: "/tmp/eicar.com", signature: "Win.Test.EICAR_HC-1", status: "detected", quarantine_path: null, note: null, detected_at: 1758800000, status_changed_at: null },
    { id: "f2", host_id: "h-mini", host_name: "mini", path: "/home/x.sh", signature: "Unix.Trojan.Mirai", status: "quarantined", quarantine_path: "/var/lib/quarantine/f2_x.sh", note: null, detected_at: 1758700000, status_changed_at: 1758700001 },
  ];
  const ROOT_ERROR = "Keine root-Rechte: Das Dashboard ist auf diesem Server nicht als root angemeldet und darf sudo nicht ohne Passwort nutzen.";

  type Call = { url: string; method: string };
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => { resolve = r; });
    return { promise, resolve };
  }

  /** Antworten nach Endung der Adresse; alles andere kommt aus `mockFetch`, die Übersicht mit einem Server ohne ClamAV. */
  function stubRoutes(routes: Record<string, () => Response | Promise<Response>>): Call[] {
    const calls: Call[] = [];
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      const route = Object.keys(routes).find((suffix) => url.endsWith(suffix));
      if (route) return routes[route]();
      if (url.includes("/defender/overview")) return json(NO_CLAM);
      return base(input, init);
    }));
    return calls;
  }

  const installButton = async () => within(await screen.findByTestId("host-h-mini")).getByRole("button", { name: "ClamAV installieren" });

  beforeEach(() => {
    openTab("overview");
    confirmDialog.mockResolvedValue(true);
  });

  it("nennt den Grund in Rot, wenn die Automatik des Kerns die Aktion schon freigegeben hat und sie scheiterte", async () => {
    const calls = stubRoutes({
      "/hosts/h-mini/install": () => json({ action_id: "a7", status: "failed" }),
      "/actions/a7": () => json({ id: "a7", status: "failed", result: { error: "E: Paket clamav nicht gefunden" } }),
    });
    render(<SocPage />);
    fireEvent.click(await installButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("ClamAV installieren (mini): Fehlgeschlagen: E: Paket clamav nicht gefunden");
    expect(calls.some((c) => c.method === "GET" && c.url.endsWith("/actions/a7"))).toBe(true);
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
    expect(document.body.textContent).not.toContain("Status:");
  });

  it("zeigt eine von einem Menschen abgelehnte Aktion als Hinweis, nicht als Fehler", async () => {
    stubRoutes({
      "/hosts/h-mini/install": () => json({ action_id: "a9", status: "denied" }),
      "/actions/a9": () => json({ id: "a9", status: "denied", gate_decision: { rule: "user:reject", user_reason: "Nicht jetzt" } }),
    });
    render(<SocPage />);
    fireEvent.click(await installButton());

    expect(await screen.findByText("ClamAV installieren (mini): Abgelehnt: Nicht jetzt")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("verweist auf die Freigabe, wenn der Nutzer nicht selbst freigeben darf", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => p !== "actions.approve:medium");
    const calls = stubRoutes({ "/hosts/h-mini/install": () => json({ action_id: "a8", status: "proposed" }) });
    render(<SocPage />);
    fireEvent.click(await installButton());

    expect(await screen.findByText(/Wartet auf Freigabe unter „Aktionen“\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zur Freigabe" })).toHaveAttribute("href", "/actions?action=a8");
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
  });

  it("zeigt nach einer abgebrochenen Rückfrage keinen Hinweis", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls = stubRoutes({});
    render(<SocPage />);
    fireEvent.click(await installButton());
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 10)));

    expect(screen.queryByText(/abgebrochen/)).toBeNull();
    expect(screen.queryByText(/ClamAV installieren \(mini\)/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("sperrt den Knopf während der Installation, sagt, was läuft, und startet sie kein zweites Mal", async () => {
    const approve = deferred<Response>();
    const calls = stubRoutes({
      "/hosts/h-mini/install": () => json({ action_id: "a9", status: "proposed" }),
      "/actions/a9/approve": () => approve.promise,
    });
    render(<SocPage />);
    const row = within(await screen.findByTestId("host-h-mini"));
    fireEvent.click(await installButton());

    const running = await row.findByRole("button", { name: "Installiere …" });
    expect(running).toBeDisabled();
    expect(within(screen.getByTestId("next-step")).getByRole("button", { name: "Installiere …" })).toBeDisabled();
    expect(screen.getByText("ClamAV wird auf „mini“ installiert. Das kann einige Minuten dauern.")).toBeInTheDocument();
    fireEvent.click(running);
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/hosts/h-mini/install"))).toHaveLength(1);

    await act(async () => { approve.resolve(json({ status: "succeeded", result: {} })); });
    expect(await screen.findByText("ClamAV installieren (mini): Erledigt.")).toBeInTheDocument();
    expect(row.getByRole("button", { name: "ClamAV installieren" })).toBeEnabled();
  });

  it("führt bei fehlenden root-Rechten zu den Zugängen, aber nur mit dem Recht dafür", async () => {
    stubRoutes({
      "/hosts/h-mini/install": () => json({ action_id: "a10", status: "proposed" }),
      "/actions/a10/approve": () => json({ status: "failed", result: { error: ROOT_ERROR } }),
    });
    const view = render(<SocPage />);
    fireEvent.click(await installButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(ROOT_ERROR);
    expect(within(alert).getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts");
    view.unmount();

    window.__lattice.hasPermission = vi.fn((p: string) => p !== "hosts.write");
    render(<SocPage />);
    fireEvent.click(await installButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(ROOT_ERROR);
    expect(screen.queryByRole("link", { name: "Zugang einrichten" })).toBeNull();
  });

  it("sperrt in der Quarantäne die Knöpfe des Fundes, solange er wiederhergestellt wird", async () => {
    openTab("quarantine");
    const approve = deferred<Response>();
    const calls = stubRoutes({
      "/defender/findings": () => json(FINDINGS),
      "/findings/f2/restore": () => json({ action_id: "a11", status: "proposed" }),
      "/actions/a11/approve": () => approve.promise,
    });
    render(<SocPage />);
    const rows = within(await screen.findByTestId("findings"));
    fireEvent.click(await rows.findByRole("button", { name: "Wiederherstellen" }));

    const running = await rows.findByRole("button", { name: "Stelle wieder her …" });
    expect(running).toBeDisabled();
    expect(rows.getByRole("button", { name: "Endgültig löschen" })).toBeDisabled();
    expect(rows.getByRole("button", { name: "In Quarantäne" })).toBeEnabled();
    expect(screen.getByText("„/home/x.sh“ wird wiederhergestellt …")).toBeInTheDocument();
    fireEvent.click(running);
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/findings/f2/restore"))).toHaveLength(1);

    await act(async () => { approve.resolve(json({ status: "succeeded", result: {} })); });
    expect(await screen.findByText("„/home/x.sh“: Erledigt.")).toBeInTheDocument();
    expect(rows.getByRole("button", { name: "Wiederherstellen" })).toBeEnabled();
  });

  it("hält die Sperre über einen Reiterwechsel und zeigt danach den neuen Stand", async () => {
    openTab("quarantine");
    let findings = FINDINGS;
    stubRoutes({
      "/defender/findings": () => json(findings),
      "/findings/f2/restore": () => json({ action_id: "a12", status: "proposed" }),
      "/actions/a12/approve": () => json({ id: "a12", status: "executing" }),
      "/actions/a12": () => {
        findings = [FINDINGS[0], { ...FINDINGS[1], status: "restored" }];
        return json({ id: "a12", status: "succeeded", result: {} });
      },
    });
    render(<SocPage />);
    fireEvent.click(await within(await screen.findByTestId("findings")).findByRole("button", { name: "Wiederherstellen" }));
    await within(screen.getByTestId("findings")).findByRole("button", { name: "Stelle wieder her …" });

    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));
    fireEvent.click(screen.getByRole("tab", { name: /Quarantäne/ }));
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)));
    expect(within(await screen.findByTestId("findings")).getByRole("button", { name: "Stelle wieder her …" })).toBeDisabled();

    await waitFor(() => expect(within(screen.getByTestId("findings")).queryByText(/\/home\/x\.sh/)).toBeNull(), { timeout: 5000 });
    expect(screen.getByText("„/home/x.sh“: Erledigt.")).toBeInTheDocument();
  }, 10_000);

  it("meldet das Ergebnis auch, wenn inzwischen ein anderer Reiter offen ist", async () => {
    openTab("quarantine");
    const approve = deferred<Response>();
    stubRoutes({
      "/defender/findings": () => json(FINDINGS),
      "/findings/f2/restore": () => json({ action_id: "a13", status: "proposed" }),
      "/actions/a13/approve": () => approve.promise,
    });
    render(<SocPage />);
    fireEvent.click(await within(await screen.findByTestId("findings")).findByRole("button", { name: "Wiederherstellen" }));
    await within(screen.getByTestId("findings")).findByRole("button", { name: "Stelle wieder her …" });

    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));
    expect(screen.queryByTestId("findings")).toBeNull();
    expect(screen.getByText("„/home/x.sh“ wird wiederhergestellt …")).toBeInTheDocument();

    await act(async () => { approve.resolve(json({ status: "failed", result: { error: "Ursprungsort ist belegt" } })); });
    expect(await screen.findByRole("alert")).toHaveTextContent("„/home/x.sh“: Fehlgeschlagen: Ursprungsort ist belegt");
    expect(screen.queryByText("„/home/x.sh“ wird wiederhergestellt …")).toBeNull();
  });

  it("zeigt zwei Aktionen nebeneinander, jede mit ihrem eigenen Ergebnis", async () => {
    const two = { ...NO_CLAM, hosts: [MINI, { ...MINI, host_id: "h-two", host_name: "two" }] };
    const approveMini = deferred<Response>();
    const approveTwo = deferred<Response>();
    stubRoutes({
      "/defender/overview": () => json(two),
      "/defender/overview?refresh=true": () => json(two),
      "/hosts/h-mini/install": () => json({ action_id: "a14", status: "proposed" }),
      "/actions/a14/approve": () => approveMini.promise,
      "/hosts/h-two/install": () => json({ action_id: "a15", status: "proposed" }),
      "/actions/a15/approve": () => approveTwo.promise,
    });
    render(<SocPage />);
    fireEvent.click(await installButton());
    fireEvent.click(within(await screen.findByTestId("host-h-two")).getByRole("button", { name: "ClamAV installieren" }));
    expect(await screen.findByText("ClamAV wird auf „two“ installiert. Das kann einige Minuten dauern.")).toBeInTheDocument();
    expect(screen.getByText("ClamAV wird auf „mini“ installiert. Das kann einige Minuten dauern.")).toBeInTheDocument();

    await act(async () => { approveTwo.resolve(json({ status: "succeeded", result: {} })); });
    expect(await screen.findByText("ClamAV installieren (two): Erledigt.")).toBeInTheDocument();
    expect(screen.getByText("ClamAV wird auf „mini“ installiert. Das kann einige Minuten dauern.")).toBeInTheDocument();

    await act(async () => { approveMini.resolve(json({ status: "failed", result: { error: "E: Paket clamav nicht gefunden" } })); });
    expect(await screen.findByRole("alert")).toHaveTextContent("ClamAV installieren (mini): Fehlgeschlagen: E: Paket clamav nicht gefunden");
    expect(screen.getByText("ClamAV installieren (two): Erledigt.")).toBeInTheDocument();
  });

  it("meldet das Ergebnis der Fail2ban-Installation auch nach einem Reiterwechsel", async () => {
    openTab("guard");
    const guard = {
      hosts: [{
        host_id: "h-mini", host_name: "mini", host_status: "up", checking: false, open_events: 0,
        view: {
          is_root: true, ssh_log_found: true, failed_24h: 0, attacker_count: 0, banned_count: 0, fail2ban: "none", jails: {},
          attackers: [], logins: [], ports: [], files_watched: 0, files_pending: [], checked_at: 1758800000,
        },
      }],
      events: [],
      summary: { failed_24h: 0, attackers: 0, banned: 0, open_events: 0, fail2ban_running: 0, hosts: 1, new_ports: 0, files_pending: 0 },
      config: { enabled: true, interval_min: 15, threshold: 20, file_watch: true },
    };
    const approve = deferred<Response>();
    stubRoutes({
      "/defender/guard": () => json(guard),
      "/guard/check": () => json({ hosts: 1 }),
      "/hosts/h-mini/install": () => json({ action_id: "a16", status: "proposed" }),
      "/actions/a16/approve": () => approve.promise,
    });
    render(<SocPage />);
    fireEvent.click(within(await screen.findByTestId("guard-h-mini")).getByRole("button", { name: "Fail2ban installieren" }));
    const progress = "Fail2ban wird auf „mini“ installiert. Das kann einige Minuten dauern.";
    expect(await screen.findByText(progress)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));
    expect(screen.queryByTestId("guard-h-mini")).toBeNull();
    expect(screen.getByText(progress)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Einbruchschutz/ }));
    expect(within(await screen.findByTestId("guard-h-mini")).getByRole("button", { name: "Installiere …" })).toBeDisabled();
    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));

    await act(async () => { approve.resolve(json({ status: "failed", result: { error: "apt-get: Paket fail2ban nicht gefunden" } })); });
    expect(await screen.findByRole("alert")).toHaveTextContent("Fail2ban (mini): Fehlgeschlagen: apt-get: Paket fail2ban nicht gefunden");
    expect(screen.queryByText(progress)).toBeNull();
  });

  it("meldet das Ergebnis der automatischen Sicherheitsupdates auch nach einem Reiterwechsel", async () => {
    openTab("updates");
    const updates = {
      hosts: [{
        host_id: "h-mini", host_name: "mini", host_status: "up", checking: false, busy: null, last_run: null,
        status: {
          manager: "apt", count: 0, security_count: 0, reboot_required: false, reboot_reasons: [], kernel: "6.1.0-25-amd64", latest_kernel: null,
          uptime_s: 5000, unattended: false, refresh_error: null, error: null, checked_at: 1758800000, packages: [],
        },
      }],
      summary: { hosts: 1, checked: 1, up_to_date: 1, packages: 0, security: 0, reboot: 0, errors: 0 },
      runs: [],
      config: { check_enabled: true, check_cron: "0 6 * * *", auto_enabled: false, auto_mode: "security", auto_cron: "30 3 * * *", auto_reboot: false, auto_tag: null },
    };
    const approve = deferred<Response>();
    stubRoutes({
      "/defender/updates": () => json(updates),
      "/updates/check": () => json({ hosts: 1 }),
      "/hosts/h-mini/install": () => json({ action_id: "a17", status: "proposed" }),
      "/actions/a17/approve": () => approve.promise,
    });
    render(<SocPage />);
    fireEvent.click(within(await screen.findByTestId("updates-h-mini")).getByRole("button", { name: "Einrichten" }));
    const progress = "Automatische Sicherheitsupdates werden auf „mini“ eingerichtet. Das kann einige Minuten dauern.";
    expect(await screen.findByText(progress)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));
    expect(screen.queryByTestId("updates-h-mini")).toBeNull();
    expect(screen.getByText(progress)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Updates/ }));
    expect(within(await screen.findByTestId("updates-h-mini")).getByRole("button", { name: "Richte ein …" })).toBeDisabled();
    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));

    await act(async () => { approve.resolve(json({ status: "succeeded", result: {} })); });
    expect(await screen.findByText("mini: Erledigt.")).toBeInTheDocument();
    expect(screen.queryByText(progress)).toBeNull();
  });
});
