import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { HostFilter } from "./api";
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
    if (url.includes("/events/acknowledge-all")) return json({ acknowledged: 2 });
    if (url.endsWith("/hosts/h-pve2/ban")) return json({ action_id: "a1", status: "proposed" });
    if (url.endsWith("/hosts/h-pi/install")) return json({ action_id: "a2", status: "proposed" });
    if (url.includes("/actions/") && url.endsWith("/approve")) return json({ status: "succeeded", result: {} });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  window.__nodvardDeck = {
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
    (window.__nodvardDeck.hasPermission as Mock).mockReturnValue(false);
    render(<GuardTab canManage />);
    await screen.findByText(/Linux-Server mit SSH-Zugang angelegt/);
    expect(screen.queryByRole("link", { name: "Zugang einrichten" })).not.toBeInTheDocument();
  });

  it("erklärt Anfängern in einfachen Worten, was SSH und Fail2ban sind", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<GuardTab canManage />);
    const intro = await screen.findByTestId("guard-intro");
    expect(intro).toHaveTextContent(/SSH, dem Fernzugang/);
    expect(intro).toHaveTextContent(/Fail2ban ist ein kleines Programm, das Adressen sperrt, die zu oft ein falsches Passwort probieren/);
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
    expect(pi.getByText(/Zufällige UDP-Ports ohne Programmnamen zählen dann zusammen als ein Eintrag/)).toBeInTheDocument();
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

  it("nennt bei Ports ohne Programmnamen den Grund: ohne root nicht lesbar, mit root ein Kernel-Dienst", async () => {
    const base = OVERVIEW.hosts[1];
    const unreadable = { key: "udp/dyn/?", proto: "udp", address: "0.0.0.0", port: 41641, process: null, public: true, new: true, dynamic: true, count: 2, unreadable: true };
    const kernel = { key: "tcp/dyn/-", proto: "tcp", address: "0.0.0.0", port: 39259, process: null, public: true, new: false, dynamic: true, count: 4, unreadable: false };
    const overview: GuardOverview = {
      ...OVERVIEW,
      hosts: [
        { ...base, host_id: "h-user", host_name: "ohne-root", view: { ...base.view!, ports: [unreadable] } },
        { ...base, host_id: "h-root", host_name: "mit-root", view: { ...base.view!, is_root: true, ports: [kernel] } },
      ],
    };
    const events: SecurityEvent[] = [
      { ...EVENTS[0], id: "ev-user", host_id: "h-user", host_name: "ohne-root", title: "Neuer offener Port: wechselnde Ports/udp (Programm nicht lesbar)", detail: { ports: [unreadable] } },
      { ...EVENTS[0], id: "ev-root", host_id: "h-root", host_name: "mit-root", title: "Neuer offener Port: wechselnde Ports/tcp (ohne Programm)", detail: { ports: [kernel] } },
    ];
    const fallback = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/defender/guard")) return new Response(JSON.stringify(overview), { status: 200 });
      if (url.includes("/defender/events") && (init?.method ?? "GET") === "GET") return new Response(JSON.stringify(events), { status: 200 });
      return fallback(input, init);
    }));
    render(<GuardTab canManage />);

    const user = within(await screen.findByTestId("guard-h-user"));
    expect(user.getByText("wechselnde Ports/udp (2) · neu")).toHaveAttribute("title", "Zufällige Ports, ändern sich nach jedem Neustart · Programm nicht lesbar (kein root)");
    const root = within(screen.getByTestId("guard-h-root"));
    expect(root.getByText("wechselnde Ports/tcp (4)")).toHaveAttribute("title", "Zufällige Ports, ändern sich nach jedem Neustart · ohne Programm");

    const evUser = within(screen.getByTestId("event-ev-user"));
    fireEvent.click(evUser.getByText("Details"));
    expect(evUser.getByText(/Programm nicht lesbar \(kein root\)/, { selector: "span" })).toBeInTheDocument();
    expect(evUser.queryByText(/ohne Programm \(Kernel\)/)).toBeNull();
    const evRoot = within(screen.getByTestId("event-ev-root"));
    fireEvent.click(evRoot.getByText("Details"));
    expect(evRoot.getByText(/ohne Programm \(Kernel\)/)).toBeInTheDocument();
  });

  it("zeigt Ports aus einem eigenen Bereich als einen Eintrag und sagt, dass sie nicht einzeln melden", async () => {
    const base = OVERVIEW.hosts[1];
    const ranged = { key: "udp/27015-27030/srcds_linux", proto: "udp", address: "0.0.0.0", port: 27015, process: "srcds_linux", public: true, new: false, dynamic: true, count: 2, span: [27015, 27030] as [number, number] };
    const overview: GuardOverview = { ...OVERVIEW, hosts: [{ ...base, view: { ...base.view!, is_root: true, ports: [ranged] } }] };
    const fallback = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      return url.endsWith("/defender/guard") ? new Response(JSON.stringify(overview), { status: 200 }) : fallback(input, init);
    }));
    render(<GuardTab canManage />);

    const host = within(await screen.findByTestId(`guard-${base.host_id}`));
    expect(host.getByText("Ports 27015–27030/udp (2) srcds_linux")).toHaveAttribute("title", expect.stringContaining("Eigener Bereich aus den Einstellungen"));
    expect(host.getByText(/meldet der Einbruchschutz nicht einzeln\. Den Programmnamen kann sich jedes Programm selbst geben/)).toBeInTheDocument();
  });

  it("übernimmt einen neuen Port als bekannt („Ist in Ordnung“, wie bei jedem Ereignis)", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    const ev = within(await screen.findByTestId("event-ev1"));
    const button = ev.getByRole("button", { name: "Ist in Ordnung" });
    expect(button).toHaveAttribute("title", "Der Port gilt danach als bekannt und meldet sich nicht wieder.");
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText("Neuer offener Port: als in Ordnung markiert.")).toBeInTheDocument());
    expect(calls.some((c) => c.url.endsWith("/events/ev1/acknowledge") && c.method === "POST")).toBe(true);
  });

  it("sperrt eine angreifende Adresse über Fail2ban (Gate)", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    const pve2 = within(await screen.findByTestId("guard-h-pve2"));
    fireEvent.click(pve2.getByRole("button", { name: "203.0.113.9 Sperren" }));
    await waitFor(() => expect(screen.getByText("203.0.113.9 sperren: Erledigt.")).toBeInTheDocument());
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("per Fail2ban sperren"), expect.objectContaining({ danger: true }));
    expect(calls.find((c) => c.url.endsWith("/hosts/h-pve2/ban"))?.body).toEqual({ ip: "203.0.113.9", jail: "sshd", unban: false });
  });

  it("startet die Prüfung und hakt alle Ereignisse ab", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    await screen.findByTestId("guard-h-pve2");

    fireEvent.click(screen.getByRole("button", { name: /Alle jetzt prüfen/ }));
    await waitFor(() => expect(screen.getByText("Prüfung: 1 Server werden geprüft …")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Alle abhaken" }));
    await waitFor(() => expect(screen.getByText("Ereignisse: 2 abgehakt.")).toBeInTheDocument());
    expect(calls.filter((c) => c.method === "POST" && c.url.includes("acknowledge-all")).map((c) => c.url.split("/defender")[1])).toEqual(["/events/acknowledge-all"]);
  });

  it("in der Ansicht eines Servers hakt „Alle abhaken“ nur dessen Ereignisse ab", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<HostFilter.Provider value="h-pve2"><GuardTab canManage /></HostFilter.Provider>);
    await screen.findByTestId("guard-h-pve2");
    fireEvent.click(screen.getByRole("button", { name: "Alle abhaken" }));
    await screen.findByText("Ereignisse: 2 abgehakt.");
    expect(confirmDialog.mock.calls[0][0]).toMatch(/^Alle offenen Ereignisse von „pve2“ abhaken\?/);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url.split("/defender")[1])).toEqual(["/events/acknowledge-all?host=h-pve2"]);
  });

  it("„Ist in Ordnung“ sagt vorher, was übernommen wird; abgebrochen bleibt alles offen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    confirmDialog.mockResolvedValueOnce(false);
    render(<GuardTab canManage />);
    fireEvent.click(within(await screen.findByTestId("event-ev2")).getByRole("button", { name: "Ist in Ordnung" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    const [question] = confirmDialog.mock.calls[0];
    expect(question).toContain("„Wichtige Datei geändert: /usr/bin/ps“ auf „pve2“ als in Ordnung markieren?");
    expect(question).toContain("Der neue Stand der Datei gilt danach als richtig.");
    expect(question).toContain("Nur bestätigen, wenn du die Änderung selbst gemacht hast oder kennst.");
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/defender/events")).length).toBeGreaterThan(1));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("sagt, wenn beim Abhaken Ereignisse offen bleiben", async () => {
    const base = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/events/acknowledge-all")) return new Response(JSON.stringify({ acknowledged: 1, kept_open: 1 }), { status: 200 });
      return base(input, init);
    }));
    render(<GuardTab canManage />);
    await screen.findByTestId("guard-h-pve2");
    fireEvent.click(screen.getByRole("button", { name: "Alle abhaken" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Ereignisse: 1 abgehakt, 1 bleibt offen, weil ein Server/);
  });

  it("zeigt den Grund, wenn ein Server beim Bestätigen nicht antwortet", async () => {
    const reason = "pve2 ist gerade nicht erreichbar (timeout). Das Ereignis bleibt offen, damit nichts verloren geht.";
    const base = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/events/ev2/acknowledge")) return new Response(JSON.stringify({ detail: reason }), { status: 503 });
      return base(input, init);
    }));
    render(<GuardTab canManage />);
    fireEvent.click(within(await screen.findByTestId("event-ev2")).getByRole("button", { name: "Ist in Ordnung" }));
    expect(await screen.findByText(/Datei geändert: pve2 ist gerade nicht erreichbar \(timeout\)\. Das Ereignis bleibt offen/)).toBeInTheDocument();
  });

  it("ohne Verwaltungsrecht nur lesen", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<GuardTab canManage={false} />);
    await screen.findByTestId("guard-h-pve2");
    expect(screen.queryByRole("button", { name: /Sperren$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ist in Ordnung" })).toBeNull();
  });
});

describe("Ereignisse abhaken: Wortwahl je Art", () => {
  it("„Ist in Ordnung“ nur, wo ein neuer Stand übernommen wird; bei einem Angriff „Zur Kenntnis genommen“", async () => {
    const attack: SecurityEvent = {
      id: "ev3", host_id: "h-pve2", host_name: "pve2", kind: "bruteforce", kind_label: "Angriff auf SSH", severity: "critical",
      title: "Angriff auf SSH: 120 Fehlversuche", acknowledged: false, created_at: now - 10, detail: {},
    };
    const base = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/defender/events") && (init?.method ?? "GET") === "GET") return new Response(JSON.stringify([...EVENTS, attack]), { status: 200 });
      if (url.endsWith("/events/ev3/acknowledge")) return new Response(JSON.stringify({ ok: true }), { status: 200 });
      return base(input, init);
    }));
    render(<GuardTab canManage />);
    const file = within(await screen.findByTestId("event-ev2")).getByRole("button", { name: "Ist in Ordnung" });
    expect(file).toHaveAttribute("title", "Der neue Stand der Datei gilt danach als richtig.");
    const ev3 = within(screen.getByTestId("event-ev3"));
    expect(ev3.queryByRole("button", { name: "Ist in Ordnung" })).toBeNull();
    const ack = ev3.getByRole("button", { name: "Zur Kenntnis genommen" });
    expect(ack).toHaveAttribute("title", "Das Ereignis wird abgehakt. Gesperrt oder geändert wird dadurch nichts.");
    fireEvent.click(ack);
    await waitFor(() => expect(screen.getByText("Angriff auf SSH: zur Kenntnis genommen.")).toBeInTheDocument());
  });
});

describe("Ergebnis einer Aktion", () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => { resolve = r; });
    return { promise, resolve };
  }

  /** Wie `mockFetch`, aber die Freigabe antwortet, was der Test vorgibt. */
  function stubApprove(calls: Call[], approve: () => Response | Promise<Response>) {
    const base = mockFetch(calls);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/actions/") && url.endsWith("/approve")) return approve();
      return base(input, init);
    }));
  }

  const postsTo = (calls: Call[], suffix: string) => calls.filter((c) => c.method === "POST" && c.url.endsWith(suffix));

  it("lässt den Fehlschlag der Fail2ban-Installation stehen, auch wenn die Folgeprüfung antwortet", async () => {
    const calls: Call[] = [];
    stubApprove(calls, () => new Response(JSON.stringify({ status: "failed", result: { error: "apt-get: Paket fail2ban nicht gefunden" } }), { status: 200 }));
    render(<GuardTab canManage />);
    fireEvent.click(within(await screen.findByTestId("guard-h-pi")).getByRole("button", { name: "Fail2ban installieren" }));

    await waitFor(() => expect(postsTo(calls, "/guard/check")).toHaveLength(1));
    expect(postsTo(calls, "/guard/check")[0].body).toEqual({ host_ids: ["h-pi"] });
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(screen.getByRole("alert")).toHaveTextContent("Fail2ban (Raspberry Pi): Fehlgeschlagen: apt-get: Paket fail2ban nicht gefunden");
    expect(screen.queryByText(/werden geprüft/)).toBeNull();
    expect(within(screen.getByTestId("guard-h-pi")).getByRole("button", { name: "Fail2ban installieren" })).toBeEnabled();
  });

  it("sperrt beim Sperren nur den Knopf dieser Adresse und sagt, was läuft", async () => {
    const approve = deferred<Response>();
    const calls: Call[] = [];
    stubApprove(calls, () => approve.promise);
    render(<GuardTab canManage />);
    const pve2 = within(await screen.findByTestId("guard-h-pve2"));
    fireEvent.click(pve2.getByRole("button", { name: "203.0.113.9 Sperren" }));

    const running = await pve2.findByRole("button", { name: "203.0.113.9 Sperre …" });
    expect(running).toBeDisabled();
    expect(pve2.getByRole("button", { name: "198.51.100.7 entsperren" })).toBeEnabled();
    expect(screen.getByText("203.0.113.9 wird auf „pve2“ gesperrt …")).toBeInTheDocument();
    fireEvent.click(running);
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    expect(postsTo(calls, "/hosts/h-pve2/ban")).toHaveLength(1);

    await act(async () => { approve.resolve(new Response(JSON.stringify({ status: "succeeded", result: {} }), { status: 200 })); });
    expect(await screen.findByText("203.0.113.9 sperren: Erledigt.")).toBeInTheDocument();
    expect(pve2.getByRole("button", { name: "203.0.113.9 Sperren" })).toBeEnabled();
  });

  it("zeigt nach einer abgebrochenen Rückfrage keinen Hinweis", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<GuardTab canManage />);
    fireEvent.click(within(await screen.findByTestId("guard-h-pi")).getByRole("button", { name: "Fail2ban installieren" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    await act(() => new Promise((resolve) => setTimeout(resolve, 10)));

    expect(screen.queryByText(/abgebrochen/)).toBeNull();
    expect(screen.queryByText(/Fail2ban \(Raspberry Pi\)/)).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("Fail2ban: gesperrte Adressen", () => {
  type Jails = Record<string, { banned: string[] }>;

  function withJails(jails: Jails, fail2ban: "running" | "noaccess" = "running"): GuardOverview {
    const [pve2, pi] = OVERVIEW.hosts;
    return { ...OVERVIEW, hosts: [{ ...pve2, view: { ...pve2.view!, jails, fail2ban } }, pi] };
  }

  function serve(
    overview: GuardOverview | (() => GuardOverview), calls: Call[], approved: unknown = null,
    { refuse = null, events = EVENTS }: { refuse?: string | null; events?: SecurityEvent[] } = {},
  ) {
    const inner = mockFetch(calls);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
      if (url.endsWith("/defender/guard")) return json(typeof overview === "function" ? overview() : overview);
      if (url.includes("/defender/events") && (init?.method ?? "GET") === "GET") return json(events);
      if (refuse && url.endsWith("/hosts/h-pve2/ban")) {
        calls.push({ url, method: "POST", body: JSON.parse(init!.body as string) });
        return json({ detail: refuse }, 409);
      }
      if (approved && url.includes("/actions/") && url.endsWith("/approve")) return new Response(JSON.stringify(approved), { status: 200 });
      return inner(input, init);
    }));
  }

  const bans = (calls: Call[]) => calls.filter((c) => c.url.endsWith("/hosts/h-pve2/ban")).map((c) => c.body);

  const ATTACK: SecurityEvent = {
    id: "ev9", host_id: "h-pve2", host_name: "pve2", kind: "bruteforce", kind_label: "Angriff auf SSH", severity: "warning",
    title: "40 fehlgeschlagene SSH-Anmeldungen von 1 Adresse(n)", acknowledged: false, created_at: now - 20,
    detail: { attackers: [{ ip: "203.0.113.9", count: 40, users: ["root"] }] },
  };

  it("zeigt die Adressen je Jail und entsperrt im richtigen Jail", async () => {
    const calls: Call[] = [];
    serve(withJails({ sshd: { banned: ["198.51.100.7"] }, recidive: { banned: ["203.0.113.50"] } }), calls);
    render(<GuardTab canManage />);
    const list = within(await screen.findByTestId("banned-h-pve2"));
    expect(list.getByText("Gesperrte Adressen (2)")).toBeInTheDocument();
    expect(list.getByText("Jail recidive · 1")).toBeInTheDocument();
    expect(within(screen.getByTestId("fail2ban-h-pve2")).getByText(/Ein Jail ist eine Schutzregel von Fail2ban/)).toBeInTheDocument();
    expect(list.getByText("Jail sshd · 1")).toBeInTheDocument();
    fireEvent.click(list.getByRole("button", { name: "203.0.113.50 Entsperren" }));
    await waitFor(() => expect(bans(calls)).toEqual([{ ip: "203.0.113.50", jail: "recidive", unban: true }]));
    expect(confirmDialog).toHaveBeenCalledWith("203.0.113.50 auf „pve2“ wieder freigeben (Jail „recidive“)?", expect.anything());
  });

  it("sagt ruhig, wenn die Adresse schon nicht mehr gesperrt war", async () => {
    const calls: Call[] = [];
    const note = "Die Adresse war schon nicht mehr gesperrt.";
    serve(withJails({ sshd: { banned: ["198.51.100.7"] } }), calls, { status: "succeeded", result: { success: true, detail: { note } } });
    render(<GuardTab canManage />);
    const list = within(await screen.findByTestId("banned-h-pve2"));
    fireEvent.click(list.getByRole("button", { name: "198.51.100.7 Entsperren" }));
    expect(await screen.findByText(`198.51.100.7 entsperren: ${note}`)).toBeInTheDocument();
  });

  it("entsperrt einen Angreifer in dem Jail, in dem er steht", async () => {
    const calls: Call[] = [];
    serve(withJails({ recidive: { banned: ["198.51.100.7"] }, sshd: { banned: [] } }), calls);
    render(<GuardTab canManage />);
    const pve2 = within(await screen.findByTestId("guard-h-pve2"));
    fireEvent.click(pve2.getByRole("button", { name: "198.51.100.7 entsperren" }));
    await waitFor(() => expect(bans(calls)).toEqual([{ ip: "198.51.100.7", jail: "recidive", unban: true }]));
  });

  it("sucht ab 20 Adressen und zeigt höchstens 200", async () => {
    const many = Array.from({ length: 250 }, (_, i) => `10.0.${Math.floor(i / 250)}.${i}`);
    serve(withJails({ sshd: { banned: many } }), []);
    render(<GuardTab canManage />);
    const list = within(await screen.findByTestId("banned-h-pve2"));
    expect(list.getAllByRole("button", { name: /Entsperren$/ })).toHaveLength(200);
    expect(list.getByText("… und 50 weitere, bitte über die Suche eingrenzen.")).toBeInTheDocument();
    fireEvent.change(list.getByRole("searchbox", { name: "Gesperrte Adresse suchen" }), { target: { value: "10.0.0.24" } });
    expect(list.getAllByRole("button", { name: /Entsperren$/ }).map((b) => b.getAttribute("aria-label"))).toEqual(
      ["10.0.0.24", ...Array.from({ length: 10 }, (_, i) => `10.0.0.24${i}`)].map((ip) => `${ip} Entsperren`),
    );
    fireEvent.change(list.getByRole("searchbox", { name: "Gesperrte Adresse suchen" }), { target: { value: "192." } });
    expect(list.getByText("Keine gesperrte Adresse passt zur Suche.")).toBeInTheDocument();
  });

  it("sucht ohne Rücksicht auf Groß- und Kleinschreibung, ohne Suchfeld filtert kein alter Suchtext", async () => {
    const twenty = Array.from({ length: 20 }, (_, i) => `2001:db8::${(i + 10).toString(16)}`);
    let current = withJails({ sshd: { banned: twenty } });
    serve(() => current, []);
    render(<GuardTab canManage />);
    const list = within(await screen.findByTestId("banned-h-pve2"));
    fireEvent.change(list.getByRole("searchbox", { name: "Gesperrte Adresse suchen" }), { target: { value: "2001:DB8::A" } });
    expect(list.getAllByRole("button", { name: /Entsperren$/ }).map((b) => b.getAttribute("aria-label"))).toEqual(["2001:db8::a Entsperren"]);

    current = withJails({ sshd: { banned: twenty.slice(1) } });
    fireEvent.click(list.getByRole("button", { name: "2001:db8::a Entsperren" }));
    await waitFor(() => expect(list.getByText("Gesperrte Adressen (19)")).toBeInTheDocument());
    expect(list.queryByRole("searchbox")).toBeNull();
    expect(list.getAllByRole("button", { name: /Entsperren$/ })).toHaveLength(19);
  });

  it("zeigt bei wenigen Adressen keine Suche und sagt, wenn nichts gesperrt ist", async () => {
    serve(withJails({ sshd: { banned: [] } }), []);
    render(<GuardTab canManage />);
    const list = within(await screen.findByTestId("banned-h-pve2"));
    expect(list.getByText("Gesperrte Adressen (0)")).toBeInTheDocument();
    expect(list.getByText("Fail2ban sperrt gerade keine Adresse.")).toBeInTheDocument();
    expect(list.queryByRole("searchbox")).toBeNull();
  });

  it("sperrt eine Adresse von Hand im gewählten Jail und sagt, wie man zurückkommt", async () => {
    const calls: Call[] = [];
    serve(withJails({ sshd: { banned: [] }, nginx: { banned: [] } }), calls);
    render(<GuardTab canManage />);
    const form = within(await screen.findByTestId("ban-form-h-pve2"));
    const submit = form.getByRole("button", { name: "Eingegebene Adresse sperren" });
    const field = form.getByRole("textbox", { name: "Adresse von Hand sperren" });
    expect(submit).toBeDisabled();
    expect(form.getByText(/sperrt Shield nicht\. Steht Deck hinter einem Reverse-Proxy, trag ihn unter „Vertrauenswürdiger Proxy“ ein/)).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "kein-ip" } });
    expect(submit).toBeDisabled();
    expect((form.getByRole("combobox", { name: "Jail" }) as HTMLSelectElement).value).toBe("sshd");
    fireEvent.change(field, { target: { value: " 2001:db8::7 " } });
    fireEvent.change(form.getByRole("combobox", { name: "Jail" }), { target: { value: "nginx" } });
    fireEvent.click(submit);
    await waitFor(() => expect(bans(calls)).toEqual([{ ip: "2001:db8::7", jail: "nginx", unban: false }]));
    expect(confirmDialog).toHaveBeenCalledWith(
      "2001:db8::7 auf „pve2“ per Fail2ban sperren (Jail „nginx“)? Diese Adresse erreicht dann nichts mehr, was der Jail "
        + "„nginx“ schützt. Die Sperre endet meist nach der Sperrzeit des Jails von selbst; früher zurück geht es hier unter "
        + "„Gesperrte Adressen“ mit „Entsperren“.",
      expect.objectContaining({ danger: true }),
    );
    await waitFor(() => expect(field).toHaveValue(""));
  });

  it("behält die Eingabe, wenn die Rückfrage abgelehnt wird", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls: Call[] = [];
    serve(withJails({ sshd: { banned: [] } }), calls);
    render(<GuardTab canManage />);
    const form = within(await screen.findByTestId("ban-form-h-pve2"));
    expect(form.queryByRole("combobox", { name: "Jail" })).toBeNull();
    fireEvent.change(form.getByRole("textbox", { name: "Adresse von Hand sperren" }), { target: { value: "203.0.113.77" } });
    fireEvent.click(form.getByRole("button", { name: "Eingegebene Adresse sperren" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    expect(confirmDialog.mock.calls[0][0]).toContain("Von dieser Adresse ist dann keine SSH-Verbindung mehr möglich.");
    expect(bans(calls)).toEqual([]);
    expect(form.getByRole("textbox", { name: "Adresse von Hand sperren" })).toHaveValue("203.0.113.77");
  });

  it("behält die Eingabe, wenn das Sperren scheitert", async () => {
    const calls: Call[] = [];
    const refuse = "Von dieser Adresse aus nutzt du gerade Nodvard Deck.";
    serve(withJails({ sshd: { banned: [] } }), calls, null, { refuse });
    render(<GuardTab canManage />);
    const form = within(await screen.findByTestId("ban-form-h-pve2"));
    fireEvent.change(form.getByRole("textbox", { name: "Adresse von Hand sperren" }), { target: { value: "203.0.113.77" } });
    fireEvent.click(form.getByRole("button", { name: "Eingegebene Adresse sperren" }));
    expect(await screen.findByText(`203.0.113.77 sperren: ${refuse}`)).toBeInTheDocument();
    expect(bans(calls)).toEqual([{ ip: "203.0.113.77", jail: "sshd", unban: false }]);
    expect(form.getByRole("textbox", { name: "Adresse von Hand sperren" })).toHaveValue("203.0.113.77");
  });

  it("sagt, warum eine Eingabe keine Adresse zum Sperren ist", async () => {
    serve(withJails({ sshd: { banned: [] } }), []);
    render(<GuardTab canManage />);
    const form = within(await screen.findByTestId("ban-form-h-pve2"));
    const field = form.getByRole("textbox", { name: "Adresse von Hand sperren" });
    const hint = "Nur eine einzelne IP-Adresse, keine Bereiche wie /24 und keine Namen.";
    for (const value of ["203.0.113.0/24", "server.local"]) {
      fireEvent.change(field, { target: { value } });
      expect(form.getByText(hint)).toBeInTheDocument();
      expect(form.getByRole("button", { name: "Eingegebene Adresse sperren" })).toBeDisabled();
    }
    fireEvent.change(field, { target: { value: "2001:DB8::7" } });
    expect(form.queryByText(hint)).toBeNull();
    expect(form.getByRole("button", { name: "Eingegebene Adresse sperren" })).toBeEnabled();
  });

  it("bietet ohne Jail für SSH bei Angreifern kein Sperren an und sagt, warum", async () => {
    serve(withJails({ nginx: { banned: [] } }), [], null, { events: [ATTACK] });
    render(<GuardTab canManage />);
    const pve2 = within(await screen.findByTestId("guard-h-pve2"));
    expect(pve2.queryByRole("button", { name: "203.0.113.9 Sperren" })).toBeNull();
    expect(pve2.getByText(/erst, wenn Fail2ban einen Jail für SSH \(meist „sshd“\) eingeschaltet hat/)).toBeInTheDocument();
    expect(pve2.getByRole("button", { name: "198.51.100.7 entsperren" })).toBeInTheDocument();
    const ev = within(await screen.findByTestId("event-ev9"));
    fireEvent.click(ev.getByRole("button", { name: "Details" }));
    expect(ev.getByText("203.0.113.9")).toBeInTheDocument();
    expect(ev.queryByRole("button", { name: "203.0.113.9 Sperren" })).toBeNull();
  });

  it("Sperren aus einem Ereignis nur mit Verwaltungsrecht und im SSH-Jail", async () => {
    const calls: Call[] = [];
    serve(withJails({ nginx: { banned: [] }, sshd: { banned: [] } }), calls, null, { events: [ATTACK] });
    const view = render(<GuardTab canManage={false} />);
    let ev = within(await screen.findByTestId("event-ev9"));
    fireEvent.click(ev.getByRole("button", { name: "Details" }));
    expect(ev.queryByRole("button", { name: "203.0.113.9 Sperren" })).toBeNull();
    view.unmount();

    render(<GuardTab canManage />);
    ev = within(await screen.findByTestId("event-ev9"));
    fireEvent.click(ev.getByRole("button", { name: "Details" }));
    fireEvent.click(ev.getByRole("button", { name: "203.0.113.9 Sperren" }));
    await waitFor(() => expect(bans(calls)).toEqual([{ ip: "203.0.113.9", jail: "sshd", unban: false }]));
  });

  it("ohne Verwaltungsrecht nur die Liste, ohne Knöpfe und Formular", async () => {
    serve(withJails({ sshd: { banned: ["198.51.100.7"] } }), []);
    render(<GuardTab canManage={false} />);
    const list = within(await screen.findByTestId("banned-h-pve2"));
    expect(list.getByText("198.51.100.7")).toBeInTheDocument();
    expect(list.queryByRole("button", { name: /Entsperren$/ })).toBeNull();
    expect(screen.queryByTestId("ban-form-h-pve2")).toBeNull();
  });

  it("zeigt nichts davon ohne lesbares Fail2ban und erklärt einen Fail2ban ohne Jail", async () => {
    serve(withJails({}, "noaccess"), []);
    const view = render(<GuardTab canManage />);
    await screen.findByTestId("guard-h-pve2");
    expect(screen.queryByTestId("fail2ban-h-pve2")).toBeNull();
    expect(screen.queryByTestId("fail2ban-h-pi")).toBeNull();
    view.unmount();
    serve(withJails({}), []);
    render(<GuardTab canManage />);
    const section = within(await screen.findByTestId("fail2ban-h-pve2"));
    expect(section.getByText("Fail2ban läuft, hat aber keinen Jail eingeschaltet und sperrt deshalb nichts.")).toBeInTheDocument();
    expect(screen.queryByTestId("ban-form-h-pve2")).toBeNull();
    expect(within(screen.getByTestId("guard-h-pve2")).queryByRole("button", { name: "203.0.113.9 Sperren" })).toBeNull();
  });
});
