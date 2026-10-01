import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { dispatchDeckEvent } from "../../../../frontend/src/lib/deckGlobal";

import { SocPage } from "./SocPage";

const OVERVIEW = {
  hosts: [
    {
      host_id: "h-pi", host_name: "Raspberry Pi", host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.0.7",
      signature_version: "27410", signature_date: "Wed Sep 24 2026", freshclam_active: true, lynis_installed: true, quarantine_files: 1,
      last_scan: { id: "s1", host_id: "h-pi", host_name: "Raspberry Pi", kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "schedule",
        status: "clean", files_scanned: 1200, infected: 0, error: null, output_tail: null, started_at: Date.now() / 1000 - 600, finished_at: null },
      last_audit: { status: "ok", hardening_index: 68, warnings: 2, created_at: 1, error: null }, scanning: false, auditing: false,
    },
    {
      host_id: "h-docker", host_name: "docker", host_status: "up", reachable: true, clamav_installed: false, lynis_installed: false,
      last_scan: null, last_audit: null, scanning: false, auditing: false,
    },
  ],
  summary: { hosts: 2, protected: 1, open_threats: 1, quarantined: 1, neutralized_total: 3, findings_30d: 2, avg_hardening: 68, score: 55 },
  config: { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" },
};

const FINDINGS = [
  { id: "f1", host_id: "h-pi", host_name: "Raspberry Pi", path: "/tmp/eicar.com", signature: "Win.Test.EICAR_HC-1", status: "detected",
    quarantine_path: null, note: null, detected_at: 1758800000, status_changed_at: null },
  { id: "f2", host_id: "h-pi", host_name: "Raspberry Pi", path: "/home/x.sh", signature: "Unix.Trojan.Mirai", status: "quarantined",
    quarantine_path: "/var/lib/nexus-quarantine/f2_x.sh", note: null, detected_at: 1758700000, status_changed_at: 1758700001 },
];

type Call = { url: string; method: string; body: unknown };

function mockFetch(calls: Call[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.includes("/defender/overview")) return json(OVERVIEW);
    if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 2, checked: 2, up_to_date: 1, packages: 3, security: 2, reboot: 0 } });
    if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 57, banned: 1, open_events: 0, fail2ban_running: 2, hosts: 2 } });
    if (url.includes("/defender/events") && method === "GET") return json([]);
    if (url.includes("/defender/findings") && method === "GET") return json(FINDINGS);
    if (url.includes("/defender/scans") && method === "GET") return json([]);
    if (url.includes("/defender/scans") && method === "POST") return json({ scans: ["s9", "s10"] });
    if (url.endsWith("/findings/f1/quarantine")) return json({ ok: true });
    if (url.endsWith("/findings/f2/restore")) return json({ action_id: "a1", status: "proposed" });
    if (url.endsWith("/hosts/h-docker/install")) return json({ action_id: "a2", status: "proposed" });
    if (url.includes("/actions/") && url.endsWith("/approve")) return json({ status: "succeeded", result: {} });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  window.history.replaceState(null, "", "/ext/nexus-soc/soc");
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("Nodvard Shield Virenschutz", () => {
  it("ohne Server: führt zu „Server & Zugänge“ statt zu einer Seite, die es nicht gibt", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
      if (url.includes("/defender/overview")) return json({ ...OVERVIEW, hosts: [], summary: { ...OVERVIEW.summary, hosts: 0 } });
      if (url.includes("/defender/findings") || url.includes("/defender/scans") || url.includes("/defender/events")) return json([]);
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
    render(<SocPage />);
    expect(await screen.findByText(/Keine Linux-Server mit SSH-Zugang gefunden\. Einrichten unter/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Einstellungen → Server & Zugänge" })).toHaveAttribute("href", "/settings/hosts");
    expect(document.body.textContent).not.toContain("Server-Seite ein");
  });

  it("zeigt Schutzwert, Kennzahlen und den Schutzstatus je Server", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    const pi = within(await screen.findByTestId("host-h-pi"));
    expect(pi.getByText("ClamAV 1.0.7")).toBeInTheDocument();
    expect(pi.getByText("sauber")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Schutzwert 55 von 100" })).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(within(screen.getByTestId("host-h-docker")).getByText("nicht installiert")).toBeInTheDocument();
  });

  it("zeigt über ?host= nur einen Server und kann zurück zu allen", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?host=h-pi");
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    const banner = within(await screen.findByTestId("host-filter"));
    await waitFor(() => expect(banner.getByText("Raspberry Pi")).toBeInTheDocument());
    expect(screen.getByTestId("host-h-pi")).toBeInTheDocument();
    expect(screen.queryByTestId("host-h-docker")).toBeNull();
    fireEvent.click(banner.getByRole("button", { name: "Alle Server anzeigen" }));
    expect(await screen.findByTestId("host-h-docker")).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("zeigt die Lage aus Updates und Einbruchschutz und springt in den Reiter", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    const side = within(await screen.findByTestId("side-summary"));
    expect(side.getByText("2 Sicherheitsupdate(s) offen")).toBeInTheDocument();
    expect(side.getByText(/ruhig · 57 SSH-Fehlversuche/)).toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /Updates/ })).getByTitle("Sicherheitsupdates")).toHaveTextContent("2");
  });

  it("startet einen Schnellscan für alle Server und installiert ClamAV über die Freigabe", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<SocPage />);
    await screen.findByTestId("host-h-pi");
    fireEvent.click(screen.getByRole("button", { name: /Schnellscan starten/ }));
    await screen.findByText(/2 Server werden geprüft/);
    expect(calls.find((c) => c.method === "POST" && c.url.includes("/defender/scans"))?.body).toEqual({ kind: "quick", host_ids: "all" });

    fireEvent.click(within(screen.getByTestId("host-h-docker")).getByRole("button", { name: "ClamAV installieren" }));
    await screen.findByText(/ClamAV installieren \(docker\): Erledigt/);
    expect(calls.find((c) => c.url.endsWith("/hosts/h-docker/install"))?.body).toEqual({ package: "clamav" });
    expect(calls.some((c) => c.url.endsWith("/actions/a2/approve"))).toBe(true);
  });

  it("warnt bei abgeschaltetem Signatur-Update und schaltet es beim Aktualisieren wieder ein", async () => {
    const calls: Call[] = [];
    const off = { ...OVERVIEW, hosts: [{ ...OVERVIEW.hosts[0], freshclam_active: false }, OVERVIEW.hosts[1]] };
    const base = mockFetch(calls);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/defender/overview")) return new Response(JSON.stringify(off), { status: 200 });
      if (url.endsWith("/hosts/h-pi/install")) {
        calls.push({ url, method: init?.method ?? "GET", body: JSON.parse(init?.body as string) });
        return new Response(JSON.stringify({ action_id: "a3", status: "proposed" }), { status: 200 });
      }
      return base(input, init);
    }));
    render(<SocPage />);
    const pi = within(await screen.findByTestId("host-h-pi"));
    expect(pi.getByText(/Auto-Update aus/)).toBeInTheDocument();
    fireEvent.click(pi.getByRole("button", { name: "Signaturen" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a3/approve"))).toBe(true));
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("automatische Signatur-Update wird dabei eingeschaltet"), expect.anything());
    expect(calls.find((c) => c.url.endsWith("/hosts/h-pi/install"))?.body).toEqual({ package: "signatures" });
  });

  it("Quarantäne: offene Funde verschieben, in Quarantäne liegende wiederherstellen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<SocPage />);
    await screen.findByTestId("host-h-pi");
    fireEvent.click(screen.getByRole("tab", { name: /Quarantäne/ }));
    const list = within(await screen.findByTestId("findings"));
    expect(list.getByText("Win.Test.EICAR_HC-1")).toBeInTheDocument();
    fireEvent.click(list.getByRole("button", { name: "In Quarantäne" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/findings/f1/quarantine"))).toBe(true));

    fireEvent.click(list.getByRole("button", { name: "Wiederherstellen" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true));
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Fehlalarm"), expect.objectContaining({ danger: true }));
  });

  it("ohne Bestätigung passiert beim Wiederherstellen nichts", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<SocPage />);
    await screen.findByTestId("host-h-pi");
    fireEvent.click(screen.getByRole("tab", { name: /Quarantäne/ }));
    fireEvent.click(within(await screen.findByTestId("findings")).getByRole("button", { name: "Wiederherstellen" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith("/findings/f2/restore"))).toBe(false);
  });
});

/** Wie die Kern-Shell: pushState (kein Browser-Ereignis) und danach die Navigations-Ereignisse (beide Namen). */
function navigateTo(url: string) {
  act(() => {
    window.history.pushState(null, "", url);
    dispatchDeckEvent("navigate");
  });
}

describe("Nodvard Shield folgt der Adresszeile", () => {
  // Abrufe, die beim Testende noch laufen (Reiter wurden gerade erst umgeschaltet), landen sonst nach
  // dem letzten act() im State und React meldet "not wrapped in act". Vor dem Aufräumen abwarten.
  afterEach(async () => {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    vi.unstubAllGlobals();
  });

  it("wechselt den Reiter, wenn ein Link auf die offene Seite ?tab= setzt", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    await screen.findByTestId("host-h-pi");
    expect(screen.getByRole("tab", { name: /Übersicht/ })).toHaveAttribute("aria-selected", "true");

    navigateTo("/ext/nexus-soc/soc?tab=quarantine");
    expect(screen.getByRole("tab", { name: /Quarantäne/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("findings")).toBeInTheDocument();

    navigateTo("/ext/nexus-soc/soc?tab=guard");
    expect(screen.getByRole("tab", { name: /Einbruchschutz/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("findings")).toBeNull();
  });

  it("folgt auch Zurück/Vor (popstate) und fällt bei unbekanntem Reiter auf die Übersicht", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=hardening");
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    expect(screen.getByRole("tab", { name: /Härtung/ })).toHaveAttribute("aria-selected", "true");

    act(() => {
      window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=unsinn");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByRole("tab", { name: /Übersicht/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("host-h-pi")).toBeInTheDocument(); // Abrufe fertig, bevor der Test endet
  });

  it("der Server-Filter folgt ?host= und lässt sich wieder entfernen", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    await screen.findByTestId("host-h-docker");
    expect(screen.queryByTestId("host-filter")).toBeNull();

    navigateTo("/ext/nexus-soc/soc?host=h-pi");
    const banner = within(screen.getByTestId("host-filter"));
    await waitFor(() => expect(banner.getByText("Raspberry Pi")).toBeInTheDocument());
    expect(screen.queryByTestId("host-h-docker")).toBeNull();

    // Anderer Server, gleiche Seite: der Filter wechselt mit.
    navigateTo("/ext/nexus-soc/soc?host=h-docker");
    await waitFor(() => expect(within(screen.getByTestId("host-filter")).getByText("docker")).toBeInTheDocument());
    expect(screen.queryByTestId("host-h-pi")).toBeNull();

    // Link ohne ?host= (z. B. Menüeintrag): alle Server wieder da.
    navigateTo("/ext/nexus-soc/soc");
    expect(screen.queryByTestId("host-filter")).toBeNull();
    expect(await screen.findByTestId("host-h-pi")).toBeInTheDocument();
  });

  it("Reiter und Server-Filter zusammen aus einem Link (wie die Einbruchschutz-Nachricht)", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    await screen.findByTestId("host-h-pi");
    navigateTo("/ext/nexus-soc/soc?tab=guard&host=h-pi");
    expect(screen.getByRole("tab", { name: /Einbruchschutz/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("host-filter")).toBeInTheDocument();
  });

  it("ein Klick auf einen Reiter schreibt ihn in die Adresszeile und behält ?host=", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?host=h-pi");
    const before = window.history.length;
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    await screen.findByTestId("host-h-pi");

    fireEvent.click(screen.getByRole("tab", { name: /Updates/ }));
    expect(window.location.search).toBe("?host=h-pi&tab=updates");
    expect(screen.getByRole("tab", { name: /Updates/ })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("tab", { name: /Übersicht/ }));
    expect(window.location.search).toBe("?host=h-pi"); // Übersicht ist Standard und braucht keinen Eintrag
    // replaceState: kein zusätzlicher Verlaufseintrag
    expect(window.history.length).toBe(before);
  });

  it("nach einem Klick in der Seite wirkt derselbe Link erneut (Router-Adresse und Seite laufen auseinander)", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=quarantine");
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    await screen.findByTestId("findings");

    fireEvent.click(screen.getByRole("tab", { name: /Scans/ }));
    expect(screen.getByRole("tab", { name: /Scans/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTestId("findings")).toBeNull();

    // Derselbe Link wie beim Öffnen: für den Router bleibt die Adresse gleich, die Seite muss trotzdem umschalten.
    navigateTo("/ext/nexus-soc/soc?tab=quarantine");
    expect(screen.getByRole("tab", { name: /Quarantäne/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("findings")).toBeInTheDocument();
  });

  it("der Knopf 'Alle Server anzeigen' entfernt nur ?host=", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=scans&host=h-pi");
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    const banner = within(await screen.findByTestId("host-filter"));
    fireEvent.click(banner.getByRole("button", { name: "Alle Server anzeigen" }));
    expect(window.location.search).toBe("?tab=scans");
    expect(screen.queryByTestId("host-filter")).toBeNull();
  });

  it("Scan-Verlauf: Marker-Zeilen (neu und alt) bleiben unsichtbar, der Rest der Ausgabe nicht", async () => {
    const scan = (id: string, tail: string | null) => ({
      id, host_id: "h-pi", host_name: "Raspberry Pi", kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "manual",
      status: "infected", files_scanned: 12, infected: 1, error: null, output_tail: tail, started_at: 1758800000, finished_at: 1758800060,
    });
    const scans = [
      scan("s-neu", "/tmp/eicar.com: Win.Test.EICAR_HC-1 FOUND\nScanned files: 12\n@@scan-rc-0123456789abcdef=1\n"),
      scan("s-alt", "/tmp/alt.sh: Unix.Trojan.Mirai FOUND\n@@nexus-rc=1\n"),
      scan("s-nur-marke", "@@scan-rc-fedcba9876543210=0\n"),
    ];
    const base = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/defender/scans") && (init?.method ?? "GET") === "GET") return new Response(JSON.stringify(scans), { status: 200 });
      return base(input, init);
    }));
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=scans");
    render(<SocPage />);
    await screen.findByTestId("scan-list");
    const scanList = () => screen.getByTestId("scan-list");
    const rows = () => within(scanList()).getAllByRole("button");
    const output = () => scanList().querySelector("pre")?.textContent ?? null;

    // Es ist immer nur ein Lauf aufgeklappt: einer nach dem anderen.
    fireEvent.click(rows()[0]);
    expect(output()).toBe("/tmp/eicar.com: Win.Test.EICAR_HC-1 FOUND\nScanned files: 12");
    fireEvent.click(rows()[1]);
    expect(output()).toBe("/tmp/alt.sh: Unix.Trojan.Mirai FOUND");
    // Nur die Marke: gar kein leerer Ausgabekasten.
    fireEvent.click(rows()[2]);
    expect(scanList().querySelector("pre")).toBeNull();
    expect(scanList().textContent).not.toContain("@@");
  });

  it("Quarantäne nennt den Tresor, aber keinen Serverpfad", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=quarantine");
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SocPage />);
    await screen.findByTestId("findings");
    expect(screen.getByText(/im Tresor auf dem Server/)).toBeInTheDocument();
    expect(screen.queryByText(/\/var\/lib\/nexus-quarantine/)).toBeNull();
  });
});
