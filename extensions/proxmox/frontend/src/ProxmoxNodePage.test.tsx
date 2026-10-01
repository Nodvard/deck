import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { navigateTo } from "../../../_shared/frontend/src/testShell";

import { ProxmoxNodePage } from "./ProxmoxNodePage";

/**
 * `window.__lattice` wird im Produktivbetrieb von frontend/src/main.tsx gesetzt
 * (Import-Map-Shim, docs/02 §5) -- hier direkt gestubbt, wie es jedes echte
 * Extension-Bundle zur Laufzeit vorfindet.
 */
const HOSTS = [
  { id: "h-vm", name: "docker", display_name: "docker", status: "running", kind: "vm", provider_ref: "pve2/qemu/pve2/100" },
  { id: "h-lxc", name: "docker-lxc", display_name: "docker-lxc", status: "running", kind: "lxc", provider_ref: "pve1/lxc/pve1/100" },
];

const METRICS = { values: { cpu_percent: 12.5, mem_used_bytes: 512 * 1024 * 1024, mem_total_bytes: 2 * 1024 ** 3, uptime_s: 3725 }, sampled_at: "2026-09-19T00:00:00Z" };

/**
 * `POST /hosts/{id}/actions/{type}` liefert bei autonomy.mode=propose (Default)
 * IMMER `status="proposed"`, nie ein direktes Ergebnis -- die alte Version dieses
 * Tests mockte faelschlich `{status: "accepted"}`, ein Wert, den das echte Backend
 * nie liefert (siehe ActionStatus-Enum), und uebersah damit die Bestaetigungs-Luecke
 * komplett.
 */
function mockFetch(
  opts: { onAction?: (url: string) => void; onApprove?: (url: string) => void; connections?: unknown[]; approveBody?: unknown } = {},
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.includes("/hosts?tag=proxmox")) {
      return new Response(JSON.stringify(HOSTS), { status: 200 });
    }
    if (url.includes("/hosts/") && url.includes("/metrics")) {
      return new Response(JSON.stringify(METRICS), { status: 200 });
    }
    if (url.endsWith("/ext/proxmox/connections") && method === "GET") {
      return new Response(JSON.stringify(opts.connections ?? []), { status: 200 });
    }
    if (url.includes("/actions/") && method === "POST" && !url.includes("/approve")) {
      opts.onAction?.(url);
      // Herunterfahren hat mittleres Risiko, alles andere hier hohes (wie capabilities.py).
      const risk = url.includes("/actions/vm.shutdown") ? "medium" : "high";
      return new Response(JSON.stringify({ id: "a1", status: "proposed", risk }), { status: 202 });
    }
    if (url.endsWith("/actions/a1/approve") && method === "POST") {
      opts.onApprove?.(url);
      return new Response(JSON.stringify(opts.approveBody ?? { id: "a1", status: "succeeded" }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string, options?: { danger?: boolean }) => Promise<boolean>>;
let hasPermission: Mock<(permission: string) => boolean>;

beforeEach(() => {
  confirmDialog = vi.fn<(message: string, options?: { danger?: boolean }) => Promise<boolean>>().mockResolvedValue(true);
  hasPermission = vi.fn<(permission: string) => boolean>().mockReturnValue(true);
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog,
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission,
  };
});

describe("ProxmoxNodePage", () => {
  it("zeigt Aktions-Buttons fuer VM- UND LXC-Hosts (Regressionstest fuer den kind==='vm'-Bug)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ProxmoxNodePage />);

    await screen.findByText("docker");
    await screen.findByText("docker-lxc");

    // Vorher: nur die VM-Zeile hatte "Starten" -- die LXC-Zeile (pve1) blieb leer.
    expect(screen.getAllByRole("button", { name: "Starten" })).toHaveLength(2);
  });

  it("loest 'Starten' ohne Rueckfrage aus", async () => {
    let calledUrl: string | null = null;
    vi.stubGlobal("fetch", mockFetch({ onAction: (url) => (calledUrl = url) }));
    render(<ProxmoxNodePage />);

    const startButtons = await screen.findAllByRole("button", { name: "Starten" });
    fireEvent.click(startButtons[0]);

    await waitFor(() => expect(calledUrl).toContain("vm.start"));
    expect(confirmDialog).not.toHaveBeenCalled();
  });

  it("fragt bei 'Hart ausschalten' erst nach, warnt vor Datenverlust und bricht ohne Aktion ab, wenn abgelehnt", async () => {
    // vm.stop zieht in Proxmox virtuell den Stecker -- vorher hiess der Knopf
    // nur "Stoppen" mit der Rueckfrage "wirklich stoppen?".
    confirmDialog.mockResolvedValue(false);
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ProxmoxNodePage />);

    const stopButtons = await screen.findAllByRole("button", { name: "Hart ausschalten" });
    fireEvent.click(stopButtons[0]);

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(confirmDialog.mock.calls[0][0]).toBe('"docker-lxc" wirklich hart ausschalten? Das ist wie Stecker ziehen: nicht gespeicherte Daten gehen verloren.');
    expect(confirmDialog.mock.calls[0][1]).toEqual(expect.objectContaining({ danger: true }));
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/actions/vm.stop"), expect.anything());
  });

  it("'Herunterfahren' ist der normale Knopf: sauberes Herunterfahren mit Rückfrage, 'Hart ausschalten' bleibt als roter Ausweg", async () => {
    // Zweiter Teil: vm.shutdown (/status/shutdown) statt nur hartem Ausschalten.
    const calls: string[] = [];
    vi.stubGlobal("fetch", mockFetch({ onAction: (url) => calls.push(url) }));
    render(<ProxmoxNodePage />);

    const shutdownButtons = await screen.findAllByRole("button", { name: "Herunterfahren" });
    const hardButtons = screen.getAllByRole("button", { name: "Hart ausschalten" });
    expect(shutdownButtons).toHaveLength(2);
    // Erst die normale Wahl, dann (rot abgesetzt) das harte Ausschalten.
    expect(shutdownButtons[0].compareDocumentPosition(hardButtons[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(hardButtons[0].className).toContain("text-red-200");
    expect(shutdownButtons[0].className).not.toContain("red");

    fireEvent.click(shutdownButtons[0]);

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(confirmDialog.mock.calls[0][0]).toBe('"docker-lxc" sauber herunterfahren? Das Betriebssystem in der VM fährt geordnet herunter.');
    expect(confirmDialog.mock.calls[0][1]).toEqual(expect.objectContaining({ danger: false }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toContain("/hosts/h-lxc/actions/vm.shutdown");
    expect(calls[0]).not.toContain("vm.stop");
    // Mittleres Risiko -> die Seite bestätigt mit der passenden Berechtigung.
    await waitFor(() => expect(hasPermission).toHaveBeenCalledWith("actions.approve:medium"));
    await screen.findByText("docker-lxc: Herunterfahren -> abgeschlossen.");
  });

  it("'Herunterfahren': läuft der Gast nach der Wartezeit noch, steht der Hinweis des Backends auf der Seite", async () => {
    const output = "Herunterfahren angefordert, läuft nach 190s noch (der Gast ist noch nicht aus).";
    vi.stubGlobal(
      "fetch",
      mockFetch({
        approveBody: { id: "a1", status: "succeeded", result: { success: true, output, detail: { task_id: "UPID:x", task_status: "running" } } },
      }),
    );
    render(<ProxmoxNodePage />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Herunterfahren" }))[0]);

    await screen.findByText(`docker-lxc: Herunterfahren -> ${output}`);
    expect(screen.queryByText(/abgeschlossen\./)).not.toBeInTheDocument();
  });

  it("mehrere Gäste nacheinander herunterfahren: jeder Knopf bleibt bis zum eigenen Ende gesperrt, keine Meldung überschreibt die andere", async () => {
    // Herunterfahren dauert bis zu ~190 s -- man startet typischerweise mehrere. Jede
    // Freigabe hängt hier, bis der Test sie beendet.
    const finish: Record<string, () => void> = {};
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify(HOSTS), { status: 200 });
      if (url.includes("/metrics")) return new Response(JSON.stringify(METRICS), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections") && method === "GET") return new Response("[]", { status: 200 });
      const start = /\/hosts\/([^/]+)\/actions\/vm\.shutdown$/.exec(url);
      if (start && method === "POST") return new Response(JSON.stringify({ id: `a-${start[1]}`, status: "proposed", risk: "medium" }), { status: 202 });
      const approve = /\/actions\/a-([^/]+)\/approve$/.exec(url);
      if (approve && method === "POST") {
        await new Promise<void>((resolve) => { finish[approve[1]] = resolve; });
        return new Response(JSON.stringify({ id: `a-${approve[1]}`, status: "succeeded" }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    }));
    render(<ProxmoxNodePage />);

    const [first, second] = await screen.findAllByRole("button", { name: "Herunterfahren" });
    fireEvent.click(first);
    await waitFor(() => expect(first).toHaveTextContent("…"));
    // Zweiter Gast, während der erste noch herunterfährt: der erste Knopf bleibt gesperrt.
    fireEvent.click(second);
    await waitFor(() => expect(second).toHaveTextContent("…"));
    expect(first).toBeDisabled();
    expect(first).toHaveTextContent("…");
    expect(Object.keys(finish).sort()).toEqual(["h-lxc", "h-vm"]);

    // Der erste ist zuerst fertig: sein Knopf wird frei, der des zweiten bleibt gesperrt.
    finish["h-lxc"]();
    await screen.findByText("docker-lxc: Herunterfahren -> abgeschlossen.");
    await waitFor(() => expect(first).not.toBeDisabled());
    expect(first).toHaveTextContent("Herunterfahren");
    expect(second).toBeDisabled();
    expect(second).toHaveTextContent("…");

    // Danach der zweite: beide Meldungen stehen nebeneinander da.
    finish["h-vm"]();
    await screen.findByText("docker: Herunterfahren -> abgeschlossen.");
    await waitFor(() => expect(second).not.toBeDisabled());
    expect(screen.getByText("docker-lxc: Herunterfahren -> abgeschlossen.")).toBeInTheDocument();
  });

  it("eine neue Aktion räumt die Meldungen fertiger Aktionen weg, damit keine alten Zeilen stehen bleiben", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ProxmoxNodePage />);

    const [first, second] = await screen.findAllByRole("button", { name: "Starten" });
    fireEvent.click(first);
    await screen.findByText("docker-lxc: Starten -> abgeschlossen.");
    // Die nächste Aktion (anderer Host) beginnt: die Meldung der fertigen verschwindet.
    fireEvent.click(second);
    await screen.findByText("docker: Starten -> abgeschlossen.");
    expect(screen.queryByText("docker-lxc: Starten -> abgeschlossen.")).not.toBeInTheDocument();
  });

  it("'Herunterfahren' ohne Bestätigung im Dialog: nichts wird ausgelöst", async () => {
    confirmDialog.mockResolvedValue(false);
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ProxmoxNodePage />);

    fireEvent.click((await screen.findAllByRole("button", { name: "Herunterfahren" }))[0]);

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/actions/vm.shutdown"), expect.anything());
  });

  it("bestätigt einen eigenen Vorschlag automatisch, wenn der Nutzer die Berechtigung hat", async () => {
    let approved = false;
    vi.stubGlobal("fetch", mockFetch({ onApprove: () => (approved = true) }));
    render(<ProxmoxNodePage />);

    const stopButtons = await screen.findAllByRole("button", { name: "Hart ausschalten" });
    fireEvent.click(stopButtons[0]);

    await waitFor(() => expect(approved).toBe(true));
    expect(hasPermission).toHaveBeenCalledWith("actions.approve:high");
    await screen.findByText(/abgeschlossen/);
  });

  it("bestätigt NICHT automatisch ohne die noetige Berechtigung -- verweist auf 'Aktionen'", async () => {
    hasPermission.mockReturnValue(false);
    let approveCalled = false;
    vi.stubGlobal("fetch", mockFetch({ onApprove: () => (approveCalled = true) }));
    render(<ProxmoxNodePage />);

    const stopButtons = await screen.findAllByRole("button", { name: "Hart ausschalten" });
    fireEvent.click(stopButtons[0]);

    await screen.findByText(/Freigabe durch einen Admin nötig/);
    expect(approveCalled).toBe(false);
  });

  it("zeigt echte Live-Metriken beim Aufklappen einer VM-Zeile", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ProxmoxNodePage />);

    const toggle = (await screen.findAllByLabelText("Details anzeigen"))[0];
    fireEvent.click(toggle);

    await screen.findByText("12.5 %");
    expect(screen.getByText(/512.*MB.*\/.*2\.0 GB/)).toBeInTheDocument();
  });

  it("gruppiert Hosts nach Proxmox-Verbindung (Multi-Instanz)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ProxmoxNodePage />);

    await screen.findByText("docker"); // warten, bis geladen ist
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["pve1", "pve2"]);
  });
});

/**
 * Verbindungen über API/UI verwalten: bisher gab es dafuer keine Oberflaeche, nur direkten
 * DB-Schreibzugriff -- `ConnectionsPanel` schliesst diese Luecke.
 */
describe("ProxmoxNodePage ConnectionsPanel", () => {
  it("legt eine neue Verbindung an und fordert danach ein Token an", async () => {
    let created: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify([]), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections") && method === "GET") {
        return new Response(JSON.stringify(created ? [created] : []), { status: 200 });
      }
      if (url.endsWith("/ext/proxmox/connections") && method === "POST") {
        created = { ...JSON.parse(init?.body as string), has_token: false };
        return new Response(JSON.stringify(created), { status: 201 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProxmoxNodePage />);

    fireEvent.click(await screen.findByRole("button", { name: "+ Neue Verbindung" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "pve2" } });
    fireEvent.change(screen.getByLabelText("Adresse (URL)"), { target: { value: "https://192.168.1.23:8006" } });
    fireEvent.change(screen.getByLabelText("Token-ID"), { target: { value: "root@pam!dashboard" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await screen.findByText(/jetzt noch ein Token setzen/);
    expect((created as { name: string }).name).toBe("pve2");
    await screen.findByText("pve2");
  });

  it("setzt ein Token ueber promptDialog, nie ueber ein Textfeld", async () => {
    let tokenValue: string | null = null;
    let tokenBody: { label: string; value: string } | null = null;
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: false };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify([]), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections") && method === "GET") return new Response(JSON.stringify([conn]), { status: 200 });
      if (url.endsWith("/extensions/proxmox/secrets") && method === "PUT") {
        tokenBody = JSON.parse(init?.body as string);
        tokenValue = tokenBody!.value;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    window.__lattice.promptDialog = vi.fn().mockResolvedValue("geheimes-token");
    render(<ProxmoxNodePage />);

    fireEvent.click(await screen.findByRole("button", { name: "Token setzen" }));

    await waitFor(() => expect(tokenValue).toBe("geheimes-token"));
    expect(tokenBody).toEqual({ label: "proxmox-token:pve2", value: "geheimes-token" });
    expect(screen.queryByRole("textbox", { name: /token/i })).not.toBeInTheDocument();
  });

  it("ersetzt ein vorhandenes Token (Knopf „Token ersetzen“, früher 409 beim zweiten Setzen)", async () => {
    let put: { label: string; value: string } | null = null;
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: true };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify([]), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections") && method === "GET") return new Response(JSON.stringify([conn]), { status: 200 });
      if (url.endsWith("/extensions/proxmox/secrets") && method === "PUT") {
        put = JSON.parse(init?.body as string);
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    }));
    window.__lattice.promptDialog = vi.fn().mockResolvedValue("neues-token");
    render(<ProxmoxNodePage />);

    fireEvent.click(await screen.findByRole("button", { name: "Token ersetzen" }));

    await waitFor(() => expect(put).toEqual({ label: "proxmox-token:pve2", value: "neues-token" }));
    await screen.findByText('Token für "pve2" gesetzt.');
  });

  it("schaltet eine Verbindung ueber den Aktiv-Knopf um (unabhaengiges Aktivieren/Deaktivieren)", async () => {
    let lastPatch: unknown = null;
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: true };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify([]), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections") && method === "GET") return new Response(JSON.stringify([conn]), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections/pve2") && method === "PUT") {
        lastPatch = JSON.parse(init?.body as string);
        return new Response(JSON.stringify({ ...conn, enabled: false }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProxmoxNodePage />);

    fireEvent.click(await screen.findByRole("button", { name: "aktiv" }));

    await waitFor(() => expect(lastPatch).toEqual({ enabled: false }));
  });

  it("fragt vor dem Entfernen einer Verbindung nach und entfernt sie danach", async () => {
    let deleted = false;
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: true };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify([]), { status: 200 });
      if (url.endsWith("/ext/proxmox/connections") && method === "GET") {
        return new Response(JSON.stringify(deleted ? [] : [conn]), { status: 200 });
      }
      if (url.endsWith("/ext/proxmox/connections/pve2") && method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProxmoxNodePage />);

    fireEvent.click(await screen.findByRole("button", { name: "Entfernen" }));

    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("pve2"), expect.anything()));
    await waitFor(() => expect(deleted).toBe(true));
  });
  it("Konsole: Link auf die Kern-Konsole nur fuer laufende Gaeste", async () => {
    // Echte Statuswerte aus der Discovery ("up"/"down", HostStatus), nicht "running".
    const hosts = [
      { ...HOSTS[0], status: "up" },
      { ...HOSTS[1], status: "down" },
    ];
    const base = mockFetch();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify(hosts), { status: 200 });
        return base(input, init);
      }),
    );
    render(<ProxmoxNodePage />);
    await screen.findByText("docker-lxc");

    const link = screen.getByRole("link", { name: "Konsole" });
    expect(link.getAttribute("href")).toBe("/console/h-vm");
    expect(link.getAttribute("target")).toBe("_blank");
    // Gestoppter Container: kein Link, nur ein deaktivierter Hinweis.
    expect(screen.getAllByText("Konsole")).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "Konsole" })).toHaveLength(1);
  });

  it("bietet nur die Knoepfe an, die zum Zustand passen, und zeigt VM/LXC statt vm/lxc", async () => {
    const hosts = [
      { ...HOSTS[0], status: "up" },
      { ...HOSTS[1], status: "down" },
    ];
    const base = mockFetch();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify(hosts), { status: 200 });
        return base(input, init);
      }),
    );
    render(<ProxmoxNodePage />);
    await screen.findByText("docker-lxc");

    const vmRow = screen.getByText("docker").closest("tr")!;
    const lxcRow = screen.getByText("docker-lxc").closest("tr")!;
    const names = (row: HTMLElement) => [...row.querySelectorAll("button")].map((b) => b.textContent);
    // Laufende VM: kein "Starten"; gestoppter Container: weder Ausschalten noch Neustarten.
    // Das harte Ausschalten steht als Ausweg ganz hinten, "Herunterfahren" ist die normale Wahl.
    expect(names(vmRow)).toEqual(["▸", "Herunterfahren", "Neustarten", "Snapshot", "Hart ausschalten"]);
    expect(names(lxcRow)).toEqual(["▸", "Starten", "Snapshot"]);
    expect(vmRow.textContent).toContain("VM");
    expect(lxcRow.textContent).toContain("LXC");
  });

  function richFetch(calls: { url: string; method: string; body?: unknown }[]) {
    const hosts = [
      { id: "h-node", name: "pve-pve2-pve2", display_name: "Proxmox-Knoten pve2", status: "up", kind: "hypervisor", provider_ref: "pve2/node/pve2" },
      { ...HOSTS[0], status: "up" },
    ];
    const storage = {
      pools: [
        {
          id: "pve2/pve2/local", connection: "pve2", node: "pve2", storage: "local", type: "dir",
          content_labels: ["ISO-Images", "VM-Disks"], shared: false, total: 100 * 1024 ** 3, used: 46 * 1024 ** 3,
          used_percent: 46.4, tone: "good",
          volumes: [{ volid: "local:102/vm-102-disk-0.qcow2", vmid: "102", name: "Zabbix", size: 45 * 1024 ** 3 }],
          other: { backup: { label: "Backups", count: 3, size: 17 * 1024 ** 3 } },
        },
      ],
      errors: [],
    };
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      if (url.includes("/hosts?tag=proxmox")) return new Response(JSON.stringify(hosts), { status: 200 });
      if (url.endsWith("/ext/proxmox/storage")) return new Response(JSON.stringify(storage), { status: 200 });
      if (url.endsWith("/ext/proxmox/updates")) {
        return new Response(JSON.stringify({
          nodes: [
            {
              connection: "pve2", node: "pve2", error: null, count: 2, badge: "2 Updates", tone: "warn",
              summary: "2 Updates verfügbar, darunter ein neuer Kernel (danach Neustart nötig)",
              pve_version: "9.2.20", running_kernel: "7.0.14-16-pve", reboot_pending: false, last_check: 1790213570, last_check_ok: true,
              packages: [
                { package: "proxmox-kernel-7.0.14-19-pve-signed", title: "Proxmox Kernel Image (signed)", old_version: null, version: "7.0.14-19", new_package: true },
                { package: "pve-firewall", title: "Proxmox VE Firewall", old_version: "6.0.5", version: "6.0.6", new_package: false },
              ],
            },
            { connection: "pve1", node: "pve1", error: null, count: 0, badge: "aktuell", tone: "good", summary: "Auf dem neuesten Stand", packages: [] },
          ],
          errors: [],
        }), { status: 200 });
      }
      if (url.includes("/ext/proxmox/tasks?")) {
        const withConsole = url.includes("include_console=true");
        const base = { connection: "pve2", node: "pve2", guest_id: null, guest_name: null, user: "root@pam", endtime: null };
        const tasks = [
          ...(withConsole ? [{ ...base, upid: "UPID:pve2:5", type: "vncproxy", type_label: "Konsole geöffnet", guest_id: "100", guest_name: "docker", user: "root@pam!dashboard", status: "OK", running: false, ok: true, starttime: 1790000500, duration_s: 60 }] : []),
          { ...base, upid: "UPID:pve2:4", type: "vzdump", type_label: "Backup", guest_id: "102", guest_name: "Zabbix", status: null, running: true, ok: null, starttime: 1790000400, duration_s: null },
          { ...base, upid: "UPID:pve2:3", type: "qmstart", type_label: "VM gestartet", guest_id: "100", guest_name: "docker", status: "OK", running: false, ok: true, starttime: 1790000300, duration_s: 4 },
          { ...base, upid: "UPID:pve2:2", type: "stopall", type_label: "Alle Gäste gestoppt (Knoten fährt herunter)", status: "unexpected status", running: false, ok: false, starttime: 1790000200, duration_s: 125 },
          // Andere Verbindung -- darf nicht in der "pve2"-Gruppe landen.
          { ...base, connection: "pve1", node: "pve1", upid: "UPID:pve1:1", type: "startall", type_label: "Alle Gäste gestartet (Knotenstart)", status: "OK", running: false, ok: true, starttime: 1790000100, duration_s: 2 },
        ];
        return new Response(JSON.stringify({ tasks, errors: [] }), { status: 200 });
      }
      if (url.includes("/ext/proxmox/tasks/pve2/pve2/log?upid=")) {
        return new Response(JSON.stringify({ lines: ["starting VM 100", "TASK OK"] }), { status: 200 });
      }
      if (url.endsWith("/ext/proxmox/connections")) return new Response("[]", { status: 200 });
      if (url.includes("/metrics")) return new Response(JSON.stringify(METRICS), { status: 200 });
      if (url.includes("/ext/proxmox/nodes/h-node/health")) {
        return new Response(JSON.stringify({
          cpu_model: "AMD Ryzen 9 7940HS w/ Radeon 780M Graphics", cpu_cores: 8, cpu_threads: 16,
          loadavg: [0.22, 0.2, 0.29], io_wait_percent: 1.4,
          mem_total: 14 * 1024 ** 3, mem_available: 2 * 1024 ** 3, swap_total: 8 * 1024 ** 3, swap_used: 1024 ** 3,
          rootfs_total: 100 * 1024 ** 3, rootfs_used: 46 * 1024 ** 3, ksm_shared: 1024 ** 3, uptime_s: 3 * 86400 + 5 * 3600,
          pve_version: "9.2.20", kernel: "7.0.14-16-pve", boot_mode: "EFI", disks_error: null,
          disks: [
            { devpath: "/dev/nvme0n1", model: "Example NVMe 1TB", summary: "NVMe · 1.0 TB · 97 % Restlebensdauer · 34 °C", badge: "gesund", tone: "good", power_on_hours: 12345 },
            { devpath: "/dev/sdb", model: "Alte Platte", summary: "HDD · 2.0 TB", badge: "SMART: FAILED", tone: "danger", power_on_hours: null },
          ],
        }), { status: 200 });
      }
      if (url.includes("/ext/proxmox/guests/h-vm/details")) {
        return new Response(JSON.stringify({
          kind: "qemu", cores: 4, sockets: 1, cpu_type: "x86-64-v2-AES", memory_mb: 3072, balloon_mb: 2048, swap_mb: null,
          os: "Linux", onboot: true, startup_order: 4, bios: "SeaBIOS", machine: "q35", agent_enabled: true, agent_responding: false,
          running: true, tags: [], passthrough: ["hostpci0: 0000:01:00"], other_ips: [],
          disks: [
            { slot: "scsi0", kind: "disk", storage: "local-lvm", volume: "vm-103-disk-0", size: "102G", mountpoint: null },
            { slot: "ide2", kind: "cdrom", storage: "local", volume: "iso/debian-13.6.0-amd64-netinst.iso", size: "755M", mountpoint: null },
            { slot: "unused0", kind: "unused", storage: "local-lvm", volume: "vm-103-disk-9", size: null, mountpoint: null },
          ],
          networks: [
            { slot: "net0", name: null, model: "virtio", mac: "BC:24:11:2B:6C:40", bridge: "vmbr0", vlan: null, firewall: true, configured_ip: null, link_down: false, ips: [] },
          ],
        }), { status: 200 });
      }
      if (url.includes("/ext/proxmox/guests/h-vm/snapshots")) {
        return new Response(JSON.stringify([
          { name: "vor-update", description: "Vor dem Update", snaptime: 1790000000, parent: null, with_ram: true },
        ]), { status: 200 });
      }
      if (url.includes("/actions/vm.config_set") && method === "POST") {
        return new Response(JSON.stringify({ id: "a1", status: "proposed", risk: "medium" }), { status: 202 });
      }
      if (url.includes("/actions/vm.snapshot_") && method === "POST") {
        return new Response(JSON.stringify({ id: "a1", status: "proposed", risk: "high" }), { status: 202 });
      }
      if (url.endsWith("/actions/a1/approve")) return new Response(JSON.stringify({ id: "a1", status: "succeeded" }), { status: 200 });
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
  }

  it("zeigt Knoten (kind=hypervisor) -- vorher fehlten sie komplett", async () => {
    vi.stubGlobal("fetch", richFetch([]));
    render(<ProxmoxNodePage />);
    expect(await screen.findByText("Proxmox-Knoten pve2")).toBeInTheDocument();
  });

  it("Speicher: Belegung je Pool und welche Gast-Disks darauf liegen", async () => {
    vi.stubGlobal("fetch", richFetch([]));
    render(<ProxmoxNodePage />);
    const pool = await screen.findByTestId("pool-local");
    expect(pool.textContent).toContain("Zabbix (45.0 GB)");
    expect(pool.textContent).toContain("3 Backups (17.0 GB)");
    expect(pool.textContent).toContain("46.4 %");
    expect(within(pool).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "46.4");
  });

  it("Snapshots: aufklappen, zurueckrollen geht mit Rueckfrage ueber den Aktionsweg", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", richFetch(calls));
    confirmDialog.mockResolvedValue(true);
    render(<ProxmoxNodePage />);
    await screen.findByText("docker");

    fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));
    const list = await screen.findByTestId("snapshots-h-vm");
    expect(list.textContent).toContain("vor-update");
    expect(list.textContent).toContain("mit RAM");

    fireEvent.click(within(list).getByRole("button", { name: "Zurückrollen" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true));
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("vor-update"), expect.anything());
    const post = calls.find((c) => c.url.includes("/actions/vm.snapshot_rollback"));
    expect(post?.url).toContain("/hosts/h-vm/actions/vm.snapshot_rollback");
    expect((post?.body as { payload: unknown }).payload).toEqual({ snapname: "vor-update" });
    expect(await screen.findByText("docker: Snapshot zurückrollen „vor-update“ -> abgeschlossen.")).toBeInTheDocument();
  });

  it("fehlgeschlagene Aktion zeigt den Grund und einen deutschen Namen statt vm.snapshot_rollback", async () => {
    const base = richFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/actions/a1/approve")) {
        return new Response(JSON.stringify({ id: "a1", status: "failed", result: { success: false, error: "VM is locked (backup)\n" } }), { status: 200 });
      }
      return base(input, init);
    }));
    render(<ProxmoxNodePage />);
    await screen.findByText("docker");
    fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));
    fireEvent.click(within(await screen.findByTestId("snapshots-h-vm")).getByRole("button", { name: "Zurückrollen" }));

    expect(await screen.findByText("docker: Snapshot zurückrollen „vor-update“ -> fehlgeschlagen: VM is locked (backup)")).toBeInTheDocument();
    expect(screen.queryByText(/vm\.snapshot_rollback/)).toBeNull();
  });

  it("zwei Snapshots desselben Gasts löschen: jede Meldung bleibt, der Fehlschlag wird nicht von der späteren Erfolgsmeldung ersetzt", async () => {
    const base = richFetch([]);
    const release: Record<string, () => void> = {};
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/ext/proxmox/guests/h-vm/snapshots")) {
        return new Response(JSON.stringify([
          { name: "a", description: null, snaptime: 1790000000, parent: null, with_ram: false },
          { name: "b", description: null, snaptime: 1790000001, parent: "a", with_ram: false },
        ]), { status: 200 });
      }
      const start = /\/actions\/vm\.snapshot_delete$/.exec(url);
      if (start && method === "POST") {
        const { payload } = JSON.parse(String(init?.body)) as { payload: { snapname: string } };
        return new Response(JSON.stringify({ id: `a-${payload.snapname}`, status: "proposed", risk: "high" }), { status: 202 });
      }
      const approve = /\/actions\/a-([^/]+)\/approve$/.exec(url);
      if (approve && method === "POST") {
        if (approve[1] === "a") {
          // Das erste Löschen dauert (Proxmox sperrt den Gast).
          await new Promise<void>((resolve) => { release.a = resolve; });
          return new Response(JSON.stringify({ id: "a-a", status: "succeeded" }), { status: 200 });
        }
        return new Response(JSON.stringify({ id: "a-b", status: "failed", result: { success: false, error: "VM is locked (snapshot-delete)" } }), { status: 200 });
      }
      return base(input, init);
    }));
    render(<ProxmoxNodePage />);
    await screen.findByText("docker");
    fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));
    const list = await screen.findByTestId("snapshots-h-vm");
    const rows = within(list).getAllByRole("listitem");
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Löschen" }));
    await waitFor(() => expect(release.a).toBeDefined());
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Löschen" }));
    await screen.findByText("docker: Snapshot löschen „b“ -> fehlgeschlagen: VM is locked (snapshot-delete)");

    release.a();
    await screen.findByText("docker: Snapshot löschen „a“ -> abgeschlossen.");
    // Der Fehlschlag von „b“ steht weiter da.
    expect(screen.getByText("docker: Snapshot löschen „b“ -> fehlgeschlagen: VM is locked (snapshot-delete)")).toBeInTheDocument();
  });

  it("Aufgabenverlauf: was, wer, ob es geklappt hat -- Konsolen erst auf Wunsch, Protokoll per Klick", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", richFetch(calls));
    render(<ProxmoxNodePage />);

    const started = await screen.findByTestId("task-UPID:pve2:3");
    expect(started.textContent).toContain("VM gestartet · docker");
    expect(started.textContent).toContain("root@pam");
    expect(started.textContent).toContain("OK");
    expect(started.textContent).toContain("4s");
    expect(screen.getByTestId("task-UPID:pve2:4").textContent).toContain("läuft");
    const stopall = screen.getByTestId("task-UPID:pve2:2");
    expect(stopall.textContent).toContain("unexpected status");
    expect(stopall.textContent).toContain("2m 5s");
    // Nur die eigene Verbindung, Konsolen standardmaessig ausgeblendet.
    expect(screen.queryByTestId("task-UPID:pve1:1")).toBeNull();
    expect(screen.queryByTestId("task-UPID:pve2:5")).toBeNull();
    expect(screen.getByText("Aufgabenverlauf (3)")).toBeInTheDocument();

    fireEvent.click(started);
    expect(await screen.findByText(/starting VM 100\s+TASK OK/)).toBeInTheDocument();
    const logCall = calls.find((c) => c.url.includes("/log?upid="));
    expect(logCall?.url).toContain(`upid=${encodeURIComponent("UPID:pve2:3")}`);

    fireEvent.click(screen.getByLabelText("Konsolen-Öffnungen anzeigen"));
    expect(await screen.findByTestId("task-UPID:pve2:5")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("include_console=true"))).toBe(true);
  });

  it("Sprung von der Server-Seite (?host=&tasks=1): Gast markiert und aufgeklappt, Verlauf nur für ihn", async () => {
    window.history.replaceState({}, "", "/ext/proxmox/nodes?host=h-vm&tasks=1");
    try {
      vi.stubGlobal("fetch", richFetch([]));
      render(<ProxmoxNodePage />);
      const row = (await screen.findByText("docker")).closest("tr")!;
      // neuer und alter Klassenname (Uebergang: ein Tab mit aelterem Kern kennt nur .lattice-focus)
      expect(row.className).toContain("nodvard-deck-focus");
      expect(row.className).toContain("lattice-focus");
      expect(within(row).getByRole("button", { name: "Details einklappen" })).toBeInTheDocument();

      expect(await screen.findByText("Aufgabenverlauf (1)")).toBeInTheDocument();
      expect(screen.getByTestId("task-UPID:pve2:3")).toBeInTheDocument();
      expect(screen.queryByTestId("task-UPID:pve2:2")).toBeNull();
      expect(screen.getByText("Aufgabenverlauf (1)").closest("details")).toHaveAttribute("open");
      fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
      expect(await screen.findByText("Aufgabenverlauf (3)")).toBeInTheDocument();
      // Die volle Liste bleibt offen (nur der Filter ist weg), die Adresse behält ?host=.
      expect(screen.getByText("Aufgabenverlauf (3)").closest("details")).toHaveAttribute("open");
      expect(window.location.search).toBe("?host=h-vm");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("derselbe Link noch einmal auf die offene Seite: Filter, aufgeklappter Gast und Scrollen wie beim ersten Mal", async () => {
    const scrolled: string[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this.id); };
    window.history.replaceState({}, "", "/ext/proxmox/nodes?host=h-vm&tasks=1");
    try {
      vi.stubGlobal("fetch", richFetch([]));
      render(<ProxmoxNodePage />);
      expect(await screen.findByText("Aufgabenverlauf (1)")).toBeInTheDocument();
      await waitFor(() => expect(scrolled).toEqual(["tasks-pve2"]));
      const row = () => screen.getByText("docker", { selector: "td" }).closest("tr")!;

      // Auf der Seite verstellen: Filter weg, Gast zu, Verlauf zu.
      fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
      expect(await screen.findByText("Aufgabenverlauf (3)")).toBeInTheDocument();
      fireEvent.click(within(row()).getByRole("button", { name: "Details einklappen" }));
      (screen.getByText("Aufgabenverlauf (3)").closest("details") as HTMLDetailsElement).open = false;

      // Für den Router ist das dieselbe Adresse wie beim Öffnen -- die Seite muss trotzdem folgen.
      navigateTo("/ext/proxmox/nodes?host=h-vm&tasks=1");
      expect(await screen.findByText("Aufgabenverlauf (1)")).toBeInTheDocument();
      expect(screen.getByText("Aufgabenverlauf (1)").closest("details")).toHaveAttribute("open");
      expect(within(row()).getByRole("button", { name: "Details einklappen" })).toBeInTheDocument();
      await waitFor(() => expect(scrolled).toEqual(["tasks-pve2", "tasks-pve2"]));
    } finally {
      Element.prototype.scrollIntoView = original;
      window.history.replaceState({}, "", "/");
    }
  });

  it("derselbe Link auf einen Knoten klappt die zugeklappte Karte wieder auf", async () => {
    window.history.replaceState({}, "", "/ext/proxmox/nodes?host=h-node");
    try {
      vi.stubGlobal("fetch", richFetch([]));
      render(<ProxmoxNodePage />);
      expect(await screen.findByText(/AMD Ryzen 9 7940HS/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Knotendetails einklappen" }));
      expect(screen.queryByText(/AMD Ryzen 9 7940HS/)).toBeNull();

      navigateTo("/ext/proxmox/nodes?host=h-node");
      expect(await screen.findByText(/AMD Ryzen 9 7940HS/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Knotendetails einklappen" })).toBeInTheDocument();
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("Sprung auf einen Knoten (?host=): Karte gleich aufgeklappt mit Hardware", async () => {
    window.history.replaceState({}, "", "/ext/proxmox/nodes?host=h-node");
    try {
      vi.stubGlobal("fetch", richFetch([]));
      render(<ProxmoxNodePage />);
      expect(await screen.findByText(/AMD Ryzen 9 7940HS/)).toBeInTheDocument();
      expect(document.getElementById("host-h-node")?.className).toContain("nodvard-deck-focus");
      expect(document.getElementById("host-h-node")?.className).toContain("lattice-focus");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("Hardware ändern: nur geänderte Felder, Rückfrage, über das Gate, danach neu geladen", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", richFetch(calls));
    render(<ProxmoxNodePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Details anzeigen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Hardware ändern" }));
    const form = screen.getByTestId("edit-h-vm");
    expect(within(form).getByLabelText("RAM (MB)")).toHaveValue(3072);
    expect(within(form).getByRole("button", { name: "Speichern" })).toBeDisabled();

    fireEvent.change(within(form).getByLabelText("Kerne"), { target: { value: "6" } });
    fireEvent.click(within(form).getByLabelText("Autostart"));
    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(confirmDialog.mock.calls[0][0]).toContain("Kerne: 4 → 6, Autostart: an → aus");
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true));
    const trigger = calls.find((c) => c.url.includes("/actions/vm.config_set"));
    expect(trigger?.body).toMatchObject({ payload: { changes: { cores: 6, onboot: false } } });
    expect(await screen.findByRole("status")).toHaveTextContent("Geändert.");
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/guests/h-vm/details")).length).toBe(2));
    expect(screen.queryByTestId("edit-h-vm")).toBeNull();
  });

  it("Hardware ändern: ohne hosts.execute kein Knopf", async () => {
    hasPermission.mockImplementation((p: string) => p !== "hosts.execute");
    vi.stubGlobal("fetch", richFetch([]));
    render(<ProxmoxNodePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Details anzeigen" }));
    await screen.findByTestId("details-h-vm");
    expect(screen.queryByRole("button", { name: "Hardware ändern" })).toBeNull();
  });

  it("Updates: was je Knoten wartet, Kernel-Hinweis, Pakete aufklappbar -- nur die eigene Verbindung", async () => {
    vi.stubGlobal("fetch", richFetch([]));
    render(<ProxmoxNodePage />);

    const pve2 = await screen.findByTestId("updates-pve2");
    expect(within(pve2).getByText("2 Updates").className).toContain("amber");
    expect(pve2.textContent).toContain("darunter ein neuer Kernel (danach Neustart nötig)");
    expect(pve2.textContent).toContain("Proxmox 9.2.20 · Kernel 7.0.14-16-pve");
    expect(pve2.textContent).toContain("6.0.5 → 6.0.6");
    expect(pve2.textContent).toContain("neu: 7.0.14-19");
    expect(within(pve2).getByText("Pakete anzeigen (2)")).toBeInTheDocument();
    // pve1 gehoert zu einer anderen Verbindung ohne Hosts auf dieser Seite.
    expect(screen.queryByTestId("updates-pve1")).toBeNull();
  });

  it("Updates/Speicher: nicht erreichbare Verbindung steht als Hinweis da statt still zu fehlen", async () => {
    const base = richFetch([]);
    const dead = "GET /nodes -> All connection attempts failed";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/ext/proxmox/updates")) {
        return new Response(JSON.stringify({ nodes: [], errors: [{ connection: "pve2", error: dead }] }), { status: 200 });
      }
      if (url.endsWith("/ext/proxmox/storage")) {
        return new Response(JSON.stringify({
          pools: [],
          errors: [{ connection: "pve2", error: dead }, { connection: "pve2", node: "pve2", error: "HTTP 500" }, { connection: "pve1", error: dead }],
        }), { status: 200 });
      }
      return base(input, init);
    }));
    render(<ProxmoxNodePage />);

    const hint = await screen.findByTestId("unreachable-pve2");
    expect([...hint.querySelectorAll("p")].map((p) => p.textContent)).toEqual([
      `Updates und Speicher gerade nicht abrufbar: ${dead}`,
      "Speicher von Knoten pve2 gerade nicht abrufbar: HTTP 500",
    ]);
    // pve1 hat hier keine Hosts und damit keinen Abschnitt.
    expect(screen.queryByTestId("unreachable-pve1")).toBeNull();
  });

  it("Gast-Details: Hardware, Disks mit Speicherort, Netz, Agent -- ehrlich, wenn der Agent nicht antwortet", async () => {
    vi.stubGlobal("fetch", richFetch([]));
    render(<ProxmoxNodePage />);
    await screen.findByText("docker");

    fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));
    const details = await screen.findByTestId("details-h-vm");
    expect(details.textContent).toContain("4 vCPU (x86-64-v2-AES)");
    expect(details.textContent).toContain("3 GB, Ballooning ab 2 GB");
    expect(details.textContent).toContain("ja (Reihenfolge 4)");
    expect(within(details).getByText("eingerichtet, antwortet nicht").className).toContain("amber");
    expect(details.textContent).toContain("102G auf local-lvm");
    expect(details.textContent).toContain("CD/DVD: iso/debian-13.6.0-amd64-netinst.iso");
    expect(within(details).getByText(/Nicht zugeordnet: local-lvm:vm-103-disk-9/).closest("li")?.className).toContain("amber");
    expect(details.textContent).toContain("hostpci0: 0000:01:00");
    expect(details.textContent).toContain("vmbr0 · virtio · BC:24:11:2B:6C:40 · Firewall");
  });

  it("Knoten aufklappen: Hardware und Datenträger mit SMART, erst beim Aufklappen geladen", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", richFetch(calls));
    render(<ProxmoxNodePage />);
    await screen.findByText("Proxmox-Knoten pve2");
    expect(calls.some((c) => c.url.includes("/health"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Knotendetails anzeigen" }));
    const panel = await screen.findByTestId("node-health-h-node");
    expect(panel.textContent).toContain("AMD Ryzen 9 7940HS w/ Radeon 780M Graphics (8 Kerne / 16 Threads)");
    expect(panel.textContent).toContain("0.22 / 0.20 / 0.29 · IO-Wartezeit 1.4 %");
    expect(panel.textContent).toContain("3d 5h");
    expect(panel.textContent).toContain("Proxmox 9.2.20 · Kernel 7.0.14-16-pve · EFI");
    expect(within(panel).getByText("gesund").className).toContain("emerald");
    expect(within(panel).getByText("SMART: FAILED").className).toContain("red");
    expect(panel.textContent).toContain("97 % Restlebensdauer · 34 °C");
  });
});

describe("ProxmoxNodePage ohne Server", () => {
  function emptyFetch() {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/hosts?tag=proxmox")) return new Response("[]", { status: 200 });
      if (url.endsWith("/ext/proxmox/connections")) return new Response("[]", { status: 200 });
      throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
    }));
  }

  it("erklärt in normalem Deutsch, was fehlt, und führt zu den Moduleinstellungen (nur mit Recht)", async () => {
    emptyFetch();
    render(<ProxmoxNodePage />);
    expect(await screen.findByText("Noch keine Proxmox-Server gefunden")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Proxmox einrichten" })).toHaveAttribute("href", "/settings/extensions/proxmox");
    for (const word of ["Discovery", "Basis-URL", "entdeckt"]) expect(document.body.textContent).not.toContain(word);
  });

  it("ohne Recht extensions.manage nur der Text", async () => {
    hasPermission.mockReturnValue(false);
    emptyFetch();
    render(<ProxmoxNodePage />);
    await screen.findByText("Noch keine Proxmox-Server gefunden");
    expect(screen.queryByRole("link", { name: "Proxmox einrichten" })).toBeNull();
  });
});

