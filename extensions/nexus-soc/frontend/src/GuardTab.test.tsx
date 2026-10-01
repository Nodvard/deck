import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { GuardTab, type GuardOverview, type SecurityEvent } from "./GuardTab";

const now = Date.now() / 1000;

const OVERVIEW: GuardOverview = {
  hosts: [
    {
      host_id: "h-pve2", host_name: "pve2", host_status: "up", checking: false, open_events: 2,
      view: {
        is_root: true, ssh_log_found: true, failed_24h: 57, attacker_count: 2, banned_count: 1, fail2ban: "running",
        jails: { sshd: { banned: ["198.51.100.7"] } },
        attackers: [
          { ip: "203.0.113.9", count: 40, users: ["root", "admin"], last_ts: now - 300, banned: false },
          { ip: "198.51.100.7", count: 17, users: ["root"], last_ts: now - 900, banned: true },
        ],
        logins: [{ user: "root", ip: "192.168.1.20", method: "publickey", ts: now - 3600 }],
        ports: [
          { key: "tcp/22/sshd", proto: "tcp", address: "0.0.0.0", port: 22, process: "sshd", public: true, new: false },
          { key: "tcp/4444/nc", proto: "tcp", address: "0.0.0.0", port: 4444, process: "nc", public: true, new: true },
          { key: "udp/dyn/rpc.mountd", proto: "udp", address: "0.0.0.0", port: 37692, process: "rpc.mountd", public: true, new: false, dynamic: true, count: 6 },
        ],
        files_watched: 21, files_pending: ["/etc/passwd"], checked_at: now - 60, ssh_port: "22",
        ssh_findings: [{ key: "permitrootlogin", value: "yes", severity: "critical", text: "root darf sich mit Passwort anmelden", advice: "PermitRootLogin prohibit-password (nur mit Schlüssel)" }],
      },
    },
    {
      host_id: "h-pi", host_name: "Raspberry Pi", host_status: "up", checking: false, open_events: 0,
      view: {
        is_root: false, ssh_log_found: false, failed_24h: 0, attacker_count: 0, banned_count: 0, fail2ban: "none", jails: {},
        attackers: [], logins: [], ports: [], files_watched: 12, files_pending: [], checked_at: now - 60,
      },
    },
  ],
  summary: { failed_24h: 57, attackers: 2, banned: 1, open_events: 2, fail2ban_running: 1, hosts: 2, new_ports: 1, files_pending: 1 },
  config: { enabled: true, interval_min: 15, threshold: 20, file_watch: true },
};

const EVENTS: SecurityEvent[] = [
  {
    id: "ev1", host_id: "h-pve2", host_name: "pve2", kind: "new_port", kind_label: "Neuer offener Port", severity: "warning",
    title: "Neuer offener Port: 4444/tcp (nc)", acknowledged: false, created_at: now - 100,
    detail: { ports: [{ key: "tcp/4444/nc", proto: "tcp", address: "0.0.0.0", port: 4444, process: "nc", public: true, new: true }] },
  },
  {
    id: "ev2", host_id: "h-pve2", host_name: "pve2", kind: "file_changed", kind_label: "Datei geändert", severity: "critical",
    title: "Wichtige Datei geändert: /usr/bin/ps", acknowledged: false, created_at: now - 50,
    detail: { changes: [{ path: "/usr/bin/ps", change: "changed", severity: "critical", note: "Programmdatei passt NICHT zum installierten Paket – mögliches Rootkit!" }] },
  },
];

type Call = { url: string; method: string; body: unknown };

function mockFetch(calls: Call[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/defender/guard")) return json(OVERVIEW);
    if (url.includes("/defender/events") && method === "GET") return json(EVENTS);
    if (url.endsWith("/guard/check")) return json({ hosts: 1 });
    if (url.endsWith("/events/ev1/acknowledge")) return json({ ok: true });
    if (url.endsWith("/events/acknowledge-all")) return json({ acknowledged: 2 });
    if (url.endsWith("/hosts/h-pve2/ban")) return json({ action_id: "a1", status: "proposed" });
    if (url.endsWith("/hosts/h-pi/install")) return json({ action_id: "a2", status: "proposed" });
    if (url.includes("/actions/") && url.endsWith("/approve")) return json({ status: "succeeded", result: {} });
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

describe("Einbruchschutz", () => {
  it("ohne Server: Hinweis mit Link zu „Server & Zugänge“ (nur mit dem Recht dafür)", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/defender/guard")) return new Response(JSON.stringify({ ...OVERVIEW, hosts: [] }), { status: 200 });
      return new Response("[]", { status: 200 });
    }));
    const view = render(<GuardTab canManage />);
    expect(await screen.findByText(/Linux-Server mit SSH-Zugang angelegt/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts");
    view.unmount();
    (window.__lattice.hasPermission as Mock).mockReturnValue(false);
    render(<GuardTab canManage />);
    await screen.findByText(/Linux-Server mit SSH-Zugang angelegt/);
    expect(screen.queryByRole("link", { name: "Zugang einrichten" })).not.toBeInTheDocument();
  });

  it("zeigt Kennzahlen, Ereignisse und je Server Angreifer, Ports und Datei-Wächter", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<GuardTab canManage />);

    const pve2 = within(await screen.findByTestId("guard-h-pve2"));
    expect(screen.getByText("Fehlgeschlagene SSH-Anmeldungen")).toBeInTheDocument();
    expect(pve2.getByText("57 Fehlversuche")).toBeInTheDocument();
    expect(pve2.getByText("203.0.113.9")).toBeInTheDocument();
    expect(pve2.getByText("gesperrt")).toBeInTheDocument();
    expect(pve2.getByText("4444/tcp nc · neu")).toBeInTheDocument();
    expect(pve2.getByText("wechselnde Ports/udp (6) rpc.mountd")).toBeInTheDocument();
    expect(pve2.queryByText(/37692/)).not.toBeInTheDocument();
    expect(pve2.getByText("/etc/passwd")).toBeInTheDocument();

    const sshd = within(pve2.getByTestId("sshd-h-pve2"));
    expect(sshd.getByText("root darf sich mit Passwort anmelden")).toBeInTheDocument();
    expect(sshd.getByText(/SSH-Einstellungen · Port 22/)).toBeInTheDocument();

    const pi = within(screen.getByTestId("guard-h-pi"));
    expect(pi.getByText(/Ohne root-Rechte/)).toBeInTheDocument();
    expect(pi.getByRole("button", { name: "Fail2ban installieren" })).toBeInTheDocument();

    const ev = within(screen.getByTestId("event-ev2"));
    expect(ev.getByText("kritisch")).toBeInTheDocument();
    fireEvent.click(ev.getByText("Details"));
    expect(ev.getByText(/mögliches Rootkit/)).toBeInTheDocument();
  });

  it("zeigt „sicher eingestellt“ nur, wenn die SSH-Einstellungen wirklich gelesen wurden", async () => {
    const base = OVERVIEW.hosts[1];
    const host = (id: string, is_root: boolean, ssh_findings: [] | null) =>
      ({ ...base, host_id: id, host_name: id, view: { ...base.view!, is_root, ssh_log_found: true, ssh_findings } });
    const overview = { ...OVERVIEW, hosts: [host("h-ok", true, []), host("h-noroot", false, null), host("h-nosshd", true, null)] };
    const fallback = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      return url.endsWith("/defender/guard") ? new Response(JSON.stringify(overview), { status: 200 }) : fallback(input, init);
    }));
    render(<GuardTab canManage />);

    const ok = within(await screen.findByTestId("sshd-h-ok"));
    expect(ok.getByText(/Sicher eingestellt/)).toBeInTheDocument();
    const noRoot = within(screen.getByTestId("sshd-h-noroot"));
    expect(noRoot.getByText("SSH-Einstellungen nicht lesbar – dafür braucht Nodvard Deck root oder sudo ohne Passwort.")).toBeInTheDocument();
    expect(noRoot.queryByText(/Sicher eingestellt/)).toBeNull();
    const noSshd = within(screen.getByTestId("sshd-h-nosshd"));
    expect(noSshd.getByText("SSH-Einstellungen konnten nicht gelesen werden.")).toBeInTheDocument();
    expect(noSshd.queryByText(/Sicher eingestellt/)).toBeNull();
  });

  it("zeigt einen ohne root nicht lesbaren Fail2ban-Zustand nicht als gestoppt", async () => {
    const base = OVERVIEW.hosts[1];
    const overview: GuardOverview = {
      ...OVERVIEW,
      hosts: [{ ...base, view: { ...base.view!, fail2ban: "noaccess", ssh_log_found: true } }],
      summary: { ...OVERVIEW.summary, fail2ban_running: 0, fail2ban_unreadable: 1, hosts: 1 },
    };
    const fallback = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      return url.endsWith("/defender/guard") ? new Response(JSON.stringify(overview), { status: 200 }) : fallback(input, init);
    }));
    render(<GuardTab canManage />);

    expect(await screen.findByText("Fail2ban: Zustand nicht lesbar (root nötig)")).toBeInTheDocument();
    expect(screen.queryByText("Fail2ban gestoppt")).toBeNull();
    expect(screen.queryByText("ohne Fail2ban")).toBeNull();
    expect(screen.queryByRole("button", { name: "Fail2ban installieren" })).toBeNull();
    expect(screen.getByText("Fail2ban läuft auf 0/1 Servern · bei 1 nicht lesbar")).toBeInTheDocument();
  });

  it("übernimmt einen neuen Port als bekannt", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    const ev = within(await screen.findByTestId("event-ev1"));
    fireEvent.click(ev.getByRole("button", { name: "Als bekannt übernehmen" }));
    await waitFor(() => expect(screen.getByText("Neuer offener Port: als bekannt übernommen.")).toBeInTheDocument());
    expect(calls.some((c) => c.url.endsWith("/events/ev1/acknowledge") && c.method === "POST")).toBe(true);
  });

  it("sperrt eine angreifende Adresse über Fail2ban (Gate)", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    const pve2 = within(await screen.findByTestId("guard-h-pve2"));
    fireEvent.click(pve2.getByRole("button", { name: "Sperren" }));
    await waitFor(() => expect(screen.getByText("203.0.113.9 sperren: Erledigt.")).toBeInTheDocument());
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("per Fail2ban sperren"), expect.objectContaining({ danger: true }));
    expect(calls.find((c) => c.url.endsWith("/hosts/h-pve2/ban"))?.body).toEqual({ ip: "203.0.113.9", unban: false });
  });

  it("startet die Prüfung und bestätigt alle Ereignisse", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    await screen.findByTestId("guard-h-pve2");

    fireEvent.click(screen.getByRole("button", { name: /Alle jetzt prüfen/ }));
    await waitFor(() => expect(screen.getByText("Prüfung: 1 Server werden geprüft …")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Alle bestätigen" }));
    await waitFor(() => expect(screen.getByText("Ereignisse: 2 bestätigt.")).toBeInTheDocument());
  });

  it("ohne Verwaltungsrecht nur lesen", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<GuardTab canManage={false} />);
    await screen.findByTestId("guard-h-pve2");
    expect(screen.queryByRole("button", { name: "Sperren" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Als bekannt übernehmen" })).toBeNull();
  });
});
