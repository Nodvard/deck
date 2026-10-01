import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, type Mock, vi } from "vitest";

import { navigateTo } from "../../../_shared/frontend/src/testShell";

import type { LiveData } from "./LiveView";
import { formatUptime, SystemPage, type SystemInfo } from "./SystemPage";

const INFO: SystemInfo = {
  host: { id: "h-pi", name: "Raspberry Pi", address: "192.168.1.72" },
  os: "Debian GNU/Linux 13 (trixie)", kernel: "6.18.39+rpt-rpi-v8", arch: "aarch64", hostname: "Raspberry Pi",
  uptime_s: 70454, load: [0.58, 1, 0.76], cpus: 4, cpu_percent: 3,
  mem_total: 3885880 * 1024, mem_available: 2171408 * 1024, swap_total: 2097148 * 1024, swap_used: 1487428 * 1024,
  disks: [
    { device: "/dev/sda2", fstype: "ext4", mount: "/", size: 250e9, used: 70e9, available: 167e9, percent: 29.4, tone: "good" },
    { device: "/dev/sdb1", fstype: "ext4", mount: "/mnt/daten", size: 100e9, used: 95e9, available: 5e9, percent: 95, tone: "danger" },
  ],
  failed_units: ["nginx.service"],
  updates: [{ package: "linux-image-rpi-v8", security: false }, { package: "openssl", security: true }],
  reboot_required: true, temperature_c: 44.3,
  findings: [{ tone: "danger", text: "/mnt/daten zu 95 % voll" }, { tone: "warn", text: "Swap zu 71 % belegt -- RAM knapp" }],
};

const LIVE: LiveData = {
  complete: true, interval_s: 1.02, uptime_s: 70454,
  cpu: {
    model: "Raspberry Pi 4 Model B Rev 1.5", cores: 4,
    total: { percent: 37.5, user: 25, system: 10, iowait: 2.5, steal: 0 },
    per_core: [
      { id: 0, percent: 100, freq_mhz: 1800 }, { id: 1, percent: 20, freq_mhz: 1800 },
      { id: 2, percent: 15, freq_mhz: 600 }, { id: 3, percent: 15, freq_mhz: 600 },
    ],
    load: [0.58, 1, 0.76],
  },
  memory: {
    total: 3885880 * 1024, used: 1714472 * 1024, available: 2171408 * 1024, free: 400000 * 1024, buffers: 50000 * 1024,
    cached: 1600000 * 1024, shared: 20000 * 1024, dirty: 100 * 1024, swap_total: 2097148 * 1024, swap_used: 1487428 * 1024,
  },
  temperatures: [{ source: "thermal", label: "cpu-thermal", celsius: 52.1 }, { source: "nvme", label: "Composite", celsius: 41.9 }],
  fans: [],
  disks: [{ name: "sda", read_bps: 1048576, write_bps: 2097152, read_iops: 10, write_iops: 40, busy_percent: 25 }],
  network: [
    { name: "eth0", virtual: false, rx_bps: 1000000, tx_bps: 200000, rx_total: 5e9, tx_total: 1e9, errors: 0, drops: 0, speed_mbps: 1000, state: "up" },
    { name: "docker0", virtual: true, rx_bps: 500, tx_bps: 500, rx_total: 1e6, tx_total: 1e6, errors: 0, drops: 0, speed_mbps: null, state: "down" },
  ],
  filesystems: [{ device: "/dev/sda2", fstype: "ext4", mount: "/", size: 250e9, used: 80e9, available: 150e9, percent: 34.8 }],
  processes: [
    { pid: 812, name: "python3 (lattice)", user: "lattice", cpu_percent: 12.5, mem_bytes: 100e6 },
    { pid: 900, name: "pihole-FTL", user: "pihole", cpu_percent: 1.5, mem_bytes: 300e6 },
  ],
  process_count: 212,
  throttled: { raw: "0x50000", flags: ["Unterspannung (seit Start)", "gedrosselt (seit Start)"] },
};

function mockFetch(calls: string[], infoStatus = 200, info: SystemInfo = INFO) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    if (url.endsWith("/api/v1/hosts")) {
      return new Response(JSON.stringify([
        { id: "h-pi", display_name: "Raspberry Pi", address: "192.168.1.72", os_family: "linux", status: "up" },
        { id: "g-valheim", display_name: "game-win", address: "192.168.1.92", os_family: "windows", status: "running" },
      ]), { status: 200 });
    }
    if (url.includes("/ext/system/hosts/h-pi/live")) return new Response(JSON.stringify(LIVE), { status: 200 });
    if (url.includes("/ext/system/hosts/h-pi/info")) {
      return infoStatus === 200
        ? new Response(JSON.stringify(info), { status: 200 })
        : new Response(JSON.stringify({ detail: "Nicht erreichbar: timeout" }), { status: infoStatus });
    }
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

beforeEach(() => {
  // Der gewählte Server steht in der Adresszeile -- jeder Test beginnt ohne.
  window.history.replaceState({}, "", "/ext/system/system");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("SystemPage", () => {
  it("Sprung von der Server-Seite (?host=): Befunde zuerst, Platten, Dienste, Updates", async () => {
    window.history.replaceState({}, "", "/ext/system/system?host=h-pi");
    try {
      vi.stubGlobal("fetch", mockFetch([]));
      render(<SystemPage />);
      fireEvent.click(await screen.findByRole("tab", { name: "Zustand & Updates" }));
      const findings = await screen.findByTestId("findings");
      expect(findings.textContent).toContain("/mnt/daten zu 95 % voll");
      expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("System: Raspberry Pi");
      expect(screen.getByText(/Debian GNU\/Linux 13 \(trixie\) · Kernel 6\.18\.39/)).toBeInTheDocument();
      expect(screen.getByTestId("disks").textContent).toContain("95 % von 93 GB");
      expect(screen.getByTestId("failed-units").textContent).toContain("nginx.service");
      expect(screen.getByTestId("updates").textContent).toContain("2 Update(s) ausstehend, davon 1 Sicherheits-Update(s)");
      expect(screen.getByText("Neustart nötig, damit installierte Updates greifen.")).toBeInTheDocument();
      // Nur Linux-Hosts in der Auswahl.
      const options = [...screen.getByLabelText("Server wählen").querySelectorAll("option")].map((o) => o.textContent);
      expect(options).toEqual(["Server wählen …", "Raspberry Pi (192.168.1.72)"]);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("Temperatur über der Warnschwelle wird gelb markiert, darunter nicht", async () => {
    window.history.replaceState({}, "", "/ext/system/system?host=h-pi");
    try {
      vi.stubGlobal("fetch", mockFetch([], 200, { ...INFO, temperature_c: 78, temperature_tone: "warn" }));
      render(<SystemPage />);
      fireEvent.click(await screen.findByRole("tab", { name: "Zustand & Updates" }));
      expect((await screen.findByText("78 °C")).className).toContain("text-amber-300");
      cleanup();
      vi.stubGlobal("fetch", mockFetch([], 200, { ...INFO, temperature_tone: "good" }));
      render(<SystemPage />);
      fireEvent.click(await screen.findByRole("tab", { name: "Zustand & Updates" }));
      expect((await screen.findByText("44 °C")).className).not.toContain("text-amber-300");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("ohne ?host= erst wählen; Fehler ehrlich anzeigen", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, 502));
    render(<SystemPage />);
    expect(await screen.findByText(/Einen Linux-Server wählen/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("option").length).toBe(2));
    fireEvent.change(screen.getByLabelText("Server wählen"), { target: { value: "h-pi" } });
    fireEvent.click(await screen.findByRole("tab", { name: "Zustand & Updates" }));
    expect(await screen.findByText("Fehler: Nicht erreichbar: timeout")).toBeInTheDocument();
    expect(calls.filter((c) => c.includes("/info")).length).toBe(1);
  });

  it("ohne Linux-Server (z. B. frische Installation oder nur Windows): Leerzustand mit Weg zu Server & Zugänge statt eines leeren Menüs", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { id: "g-win", display_name: "Spiele-PC", address: "192.168.1.50", os_family: "windows", status: "unknown" },
    ]), { status: 200 })));
    render(<SystemPage />);
    expect(await screen.findByText("Noch kein Linux-Server")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Server & Zugänge öffnen" })).toHaveAttribute("href", "/settings/hosts");
    expect(screen.queryByText(/Einen Linux-Server wählen/)).toBeNull();
  });

  it("während die Server laden, steht noch kein Leerzustand da", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    render(<SystemPage />);
    expect(screen.queryByText("Noch kein Linux-Server")).toBeNull();
  });

  it("ein Server ohne SSH-Zugang steht in der Auswahl mit dem Hinweis dazu", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { id: "h-nas", display_name: "NAS", address: "192.168.1.20", os_family: "linux", status: "unknown", credential: null },
      { id: "h-pi", display_name: "Raspberry Pi", address: "192.168.1.72", os_family: "linux", status: "up", credential: { id: "c1", kind: "ssh_key", username: "lattice", port: 22 } },
    ]), { status: 200 })));
    render(<SystemPage />);
    await waitFor(() => expect(screen.getAllByRole("option").length).toBe(3));
    const options = [...screen.getByLabelText("Server wählen").querySelectorAll("option")].map((o) => o.textContent);
    expect(options).toEqual(["Server wählen …", "NAS (192.168.1.20) – ohne SSH-Zugang", "Raspberry Pi (192.168.1.72)"]);
  });

  it("die Auswahl schreibt ?host=; derselbe Link wirkt wieder, nachdem man hier umgeschaltet hat", async () => {
    window.history.replaceState({}, "", "/ext/system/system?host=h-pi&x=1");
    vi.stubGlobal("fetch", mockFetch([]));
    render(<SystemPage />);
    await waitFor(() => expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("System: Raspberry Pi"));

    fireEvent.change(screen.getByLabelText("Server wählen"), { target: { value: "" } });
    expect(screen.getByText(/Einen Linux-Server wählen/)).toBeInTheDocument();
    expect(window.location.search).toBe("?x=1"); // nur ?host= entfernt

    // Für den Router ist das dieselbe Adresse wie beim Öffnen -- die Seite muss trotzdem umschalten.
    navigateTo("/ext/system/system?host=h-pi&x=1");
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("System: Raspberry Pi");
    expect(screen.getByLabelText<HTMLSelectElement>("Server wählen").value).toBe("h-pi");

    fireEvent.change(screen.getByLabelText("Server wählen"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Server wählen"), { target: { value: "h-pi" } });
    expect(window.location.search).toBe("?x=1&host=h-pi");
    expect(await screen.findByText("Raspberry Pi 4 Model B Rev 1.5", { exact: false })).toBeInTheDocument();
  });

  it("Laufzeit lesbar", () => {
    expect(formatUptime(70454)).toBe("19 h 34 min");
    expect(formatUptime(3 * 86400 + 7200)).toBe("3 d 2 h");
  });

  it("Live-Ansicht wie im Task-Manager: Kerne, Speicher, Sensoren, Platten, Netz, Prozesse", async () => {
    window.history.replaceState({}, "", "/ext/system/system?host=h-pi");
    try {
      const calls: string[] = [];
      vi.stubGlobal("fetch", mockFetch(calls));
      render(<SystemPage />);
      const cores = await screen.findByTestId("cores");
      expect(cores.textContent).toContain("Kern 0100 %");
      expect(cores.textContent).toContain("1800 MHz");
      expect(screen.getByTestId("cpu-breakdown").textContent).toContain("Warten auf I/O 2.5 %");
      expect(screen.getByTestId("sensors").textContent).toContain("cpu-thermal");
      expect(screen.getByTestId("live-disks").textContent).toContain("1.0 MB/s");
      expect(screen.getByTestId("throttled").textContent).toContain("Unterspannung (seit Start)");
      // Virtuelle Schnittstellen erst auf Wunsch.
      expect(screen.getByTestId("live-network").textContent).not.toContain("docker0");
      fireEvent.click(screen.getByLabelText(/virtuelle Schnittstellen zeigen/));
      expect(screen.getByTestId("live-network").textContent).toContain("docker0");
      // Sortierung nach RAM: pihole-FTL (300 MB) vor lattice (100 MB).
      fireEvent.click(screen.getByRole("button", { name: "nach RAM" }));
      const rows = screen.getByTestId("processes").querySelectorAll("tbody tr");
      expect(rows[0].textContent).toContain("pihole-FTL");
      expect(calls.some((c) => c.includes("/info"))).toBe(false);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });
});

describe("Dienst neu starten", () => {
  interface Call { url: string; method: string; body: unknown }
  let confirmDialog: Mock<(message: string) => Promise<boolean>>;
  let permissions: Set<string>;

  const WITH_SERVICES: SystemInfo = {
    ...INFO,
    failed_units: ["nginx.service", "mnt-daten.mount"],
    running_units: ["cron.service", "nginx.service", "sshd.service"],
    restartable_units: ["nginx.service", "cron.service"],
  };

  function mockRestartFetch(calls: Call[], opts: { approvedStatus?: string; error?: string; restartStatus?: number } = {}) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (url.endsWith("/api/v1/hosts")) {
        return new Response(JSON.stringify([{ id: "h-pi", display_name: "Raspberry Pi", address: "192.168.1.72", os_family: "linux", status: "up" }]), { status: 200 });
      }
      if (url.includes("/ext/system/hosts/h-pi/live")) return new Response(JSON.stringify(LIVE), { status: 200 });
      if (url.includes("/ext/system/hosts/h-pi/info")) return new Response(JSON.stringify(WITH_SERVICES), { status: 200 });
      if (url.endsWith("/ext/system/hosts/h-pi/services/restart") && method === "POST") {
        if (opts.restartStatus && opts.restartStatus !== 200) return new Response(JSON.stringify({ detail: opts.error }), { status: opts.restartStatus });
        return new Response(JSON.stringify({ action_id: "a1", status: "proposed", risk: "medium", detail: null }), { status: 200 });
      }
      if (url.endsWith("/api/v1/actions/a1/approve")) {
        const status = opts.approvedStatus ?? "succeeded";
        return new Response(JSON.stringify(
          status === "succeeded"
            ? { id: "a1", status, result: { success: true, output: "nginx.service auf Raspberry Pi neu gestartet – Zustand jetzt: active." } }
            : { id: "a1", status, result: { success: false, error: "nginx.service auf Raspberry Pi ließ sich nicht neu starten: Unit not found." } },
        ), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch: ${url}`);
    });
  }

  async function openInfo() {
    window.history.replaceState({}, "", "/ext/system/system?host=h-pi");
    render(<SystemPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "Zustand & Updates" }));
    await screen.findByTestId("failed-units");
  }

  beforeEach(() => {
    confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
    permissions = new Set(["hosts.execute", "actions.approve:medium"]);
    window.__lattice = {
      React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
      getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: (p: string) => permissions.has(p),
    };
  });

  it("startet einen ausgefallenen Dienst nach Rückfrage neu: vorschlagen, freigeben, Ergebnis zeigen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestartFetch(calls));
    try {
      await openInfo();
      fireEvent.click(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      await waitFor(() => expect(screen.getByRole("status").textContent).toContain("nginx.service auf Raspberry Pi neu gestartet – Zustand jetzt: active."));
      expect(confirmDialog).toHaveBeenCalledWith(
        expect.stringContaining("Dienst nginx.service auf Raspberry Pi neu starten?"),
        expect.objectContaining({ confirmLabel: "Neu starten" }),
      );
      const restart = calls.find((c) => c.url.endsWith("/services/restart"));
      expect(restart?.method).toBe("POST");
      expect(restart?.body).toEqual({ unit: "nginx.service" });
      expect(calls.some((c) => c.url.endsWith("/actions/a1/approve") && c.method === "POST")).toBe(true);
      // Danach wird der Zustand neu geladen.
      await waitFor(() => expect(calls.filter((c) => c.url.includes("/info")).length).toBe(2));
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("auch in der Liste der laufenden Dienste", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestartFetch(calls));
    try {
      await openInfo();
      const list = screen.getByTestId("running-units");
      expect(list.textContent).toContain("3 laufende Dienste");
      fireEvent.click(within(list).getByRole("button", { name: "cron.service neu starten" }));
      await waitFor(() => expect(calls.some((c) => c.url.endsWith("/services/restart"))).toBe(true));
      expect(calls.find((c) => c.url.endsWith("/services/restart"))?.body).toEqual({ unit: "cron.service" });
      expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Dienst cron.service auf Raspberry Pi neu starten?"), expect.anything());
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("zeigt keinen Knopf bei Diensten, die der Server nie neu startet", async () => {
    vi.stubGlobal("fetch", mockRestartFetch([]));
    try {
      await openInfo();
      const failed = screen.getByTestId("failed-units");
      expect(failed.textContent).toContain("mnt-daten.mount");
      expect(within(failed).queryByRole("button", { name: "mnt-daten.mount neu starten" })).toBeNull();
      expect(within(failed).getByRole("button", { name: "nginx.service neu starten" })).toBeTruthy();
      const list = screen.getByTestId("running-units");
      expect(list.textContent).toContain("sshd.service");
      expect(within(list).queryByRole("button", { name: "sshd.service neu starten" })).toBeNull();
      expect(within(list).getAllByRole("button").length).toBe(2);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("bei abgebrochener Rückfrage passiert nichts", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestartFetch(calls));
    try {
      await openInfo();
      fireEvent.click(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
      expect(calls.some((c) => c.method === "POST")).toBe(false);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("ohne Freigabe-Recht bleibt es beim Vorschlag", async () => {
    permissions.delete("actions.approve:medium");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestartFetch(calls));
    try {
      await openInfo();
      fireEvent.click(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      await waitFor(() => expect(screen.getByRole("status").textContent).toContain("wartet auf Freigabe"));
      expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("zeigt Fehlschläge und Ablehnungen der Route ehrlich", async () => {
    const failed: Call[] = [];
    vi.stubGlobal("fetch", mockRestartFetch(failed, { approvedStatus: "failed" }));
    try {
      await openInfo();
      fireEvent.click(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      expect((await screen.findByRole("alert")).textContent).toContain("Fehlgeschlagen: nginx.service auf Raspberry Pi ließ sich nicht neu starten: Unit not found.");
    } finally {
      window.history.replaceState({}, "", "/");
    }
    cleanup();

    const refused: Call[] = [];
    vi.stubGlobal("fetch", mockRestartFetch(refused, { restartStatus: 409, error: "Der SSH-Dienst wird hier nicht neu gestartet." }));
    try {
      await openInfo();
      fireEvent.click(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      expect((await screen.findByRole("alert")).textContent).toContain("Der SSH-Dienst wird hier nicht neu gestartet.");
      expect(refused.some((c) => c.url.includes("/approve"))).toBe(false);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("Serverwechsel während des Neustarts: Ergebnis und Sperre gehören nicht zum neuen Server", async () => {
    const infos: string[] = [];
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/v1/hosts")) {
        return new Response(JSON.stringify([
          { id: "h-pi", display_name: "Raspberry Pi", address: "192.168.1.72", os_family: "linux", status: "up" },
          { id: "h-nas", display_name: "NAS", address: "192.168.1.50", os_family: "linux", status: "up" },
        ]), { status: 200 });
      }
      if (url.includes("/live")) return new Response(JSON.stringify(LIVE), { status: 200 });
      if (url.includes("/info")) {
        const id = url.includes("h-nas") ? "h-nas" : "h-pi";
        infos.push(id);
        return new Response(JSON.stringify({ ...WITH_SERVICES, host: { id, name: id === "h-nas" ? "NAS" : "Raspberry Pi", address: "x" } }), { status: 200 });
      }
      if (url.endsWith("/services/restart") && method === "POST") {
        await gate;
        return new Response(JSON.stringify({ action_id: "a1", status: "succeeded", risk: "medium", detail: null }), { status: 200 });
      }
      if (url.endsWith("/api/v1/actions/a1")) {
        return new Response(JSON.stringify({ id: "a1", status: "succeeded", result: { success: true, output: "nginx.service auf Raspberry Pi neu gestartet – Zustand jetzt: active." } }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    }));
    try {
      await openInfo();
      fireEvent.click(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      await waitFor(() => expect(within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }).textContent).toBe("Startet neu …"));
      fireEvent.change(screen.getByLabelText("Server wählen"), { target: { value: "h-nas" } });
      await waitFor(() => expect(infos).toEqual(["h-pi", "h-nas"]));
      // Auf dem anderen Server ist nichts gesperrt.
      const button = await waitFor(() => within(screen.getByTestId("failed-units")).getByRole("button", { name: "nginx.service neu starten" }));
      expect((button as HTMLButtonElement).disabled).toBe(false);
      release();
      await new Promise((r) => setTimeout(r, 50));
      expect(screen.queryByRole("status")).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      // Der alte Server wird nicht mehr nachgeladen.
      expect(infos).toEqual(["h-pi", "h-nas"]);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("ohne Recht zum Vorschlagen gibt es keine Knöpfe", async () => {
    permissions.clear();
    vi.stubGlobal("fetch", mockRestartFetch([]));
    try {
      await openInfo();
      expect(screen.getByTestId("failed-units").textContent).toBe("nginx.servicemnt-daten.mount");
      expect(screen.queryByRole("button", { name: /neu starten/ })).toBeNull();
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });
});
