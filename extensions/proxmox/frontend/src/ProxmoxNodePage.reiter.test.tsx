import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ProxmoxNodePage } from "./ProxmoxNodePage";

/** Proxmox-Seite: oben das Wichtigste als Kacheln, darunter Reiter statt einer langen Liste. */

const GB = 1024 ** 3;
const HOSTS = [
  { id: "h-node", name: "pve-pve2-pve2", display_name: "pve2", status: "up", kind: "hypervisor", provider_ref: "pve2/node/pve2" },
  { id: "h-node2", name: "pve-pve2-pve3", display_name: "pve3", status: "down", kind: "hypervisor", provider_ref: "pve2/node/pve3" },
  { id: "h-vm", name: "docker", display_name: "docker", status: "up", kind: "vm", provider_ref: "pve2/qemu/pve2/100" },
  { id: "h-lxc", name: "dns", display_name: "dns", status: "down", kind: "lxc", provider_ref: "pve2/lxc/pve2/101" },
];
const pool = (storage: string, node: string, used_percent: number, tone: string) => ({
  id: `pve2/${node}/${storage}`, connection: "pve2", node, storage, type: "dir", content_labels: ["VM-Disks"], shared: false,
  total: 100 * GB, used: used_percent * GB, used_percent, tone, volumes: [],
});
const UPDATES = {
  nodes: [
    { connection: "pve2", node: "pve2", error: null, count: 3, badge: "3 Updates", tone: "warn", summary: "3 Updates verfügbar", reboot_pending: true, packages: [] },
    { connection: "pve2", node: "pve3", error: null, count: 1, badge: "1 Update", tone: "warn", summary: "1 Update verfügbar", packages: [] },
    // Andere Verbindung ohne Hosts auf dieser Seite: zählt nicht mit.
    { connection: "pve9", node: "pve9", error: null, count: 7, badge: "7 Updates", tone: "warn", summary: "7 Updates verfügbar", packages: [] },
  ],
  errors: [],
};

let calls: string[];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url}`);
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (url.includes("/hosts?tag=proxmox")) return json(HOSTS);
    if (url.endsWith("/ext/proxmox/connections")) return json([]);
    if (url.endsWith("/ext/proxmox/updates")) return json(UPDATES);
    if (url.endsWith("/ext/proxmox/storage")) {
      return json({ pools: [pool("local", "pve2", 41, "good"), pool("local-lvm", "pve2", 93.4, "danger"), pool("local", "pve3", 12, "good")], errors: [] });
    }
    if (url.includes("/ext/proxmox/tasks?")) return json({ tasks: [], errors: [] });
    if (url.includes("/actions/vm.reboot") && method === "POST") return json({ id: "a1", status: "proposed", risk: "high" }, 202);
    if (url.endsWith("/actions/a1/approve")) return json({ id: "a1", status: "succeeded" });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string, options?: { danger?: boolean }) => Promise<boolean>>;

beforeEach(() => {
  calls = [];
  window.history.replaceState({}, "", "/ext/proxmox/nodes");
  confirmDialog = vi.fn<(message: string, options?: { danger?: boolean }) => Promise<boolean>>().mockResolvedValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", mockFetch());
});

describe("Kacheln oben", () => {
  it("zeigen Knoten, Gäste, Updates und den vollsten Speicher -- ein Klick öffnet den Reiter", async () => {
    render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-storage")).toHaveTextContent("93 %"));

    expect(screen.getByTestId("tile-nodes")).toHaveTextContent("1/2");
    expect(screen.getByTestId("tile-guests")).toHaveTextContent("1/2");
    expect(screen.getByTestId("tile-guests")).toHaveTextContent("laufen · 1 gestoppt");
    // 3 + 1 Pakete; pve9 gehört zu keiner Verbindung dieser Seite.
    expect(screen.getByTestId("tile-updates")).toHaveTextContent("4");
    expect(screen.getByTestId("tile-updates")).toHaveTextContent("Pakete warten · Neustart nötig");
    expect(screen.getByTestId("tile-storage")).toHaveTextContent("am vollsten: local-lvm (pve2)");
    expect(screen.getByRole("tab", { name: /^Updates/ })).toHaveTextContent("Updates4");

    expect(screen.getByRole("tab", { name: "VMs & LXC" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByTestId("tile-storage"));
    expect(screen.getByRole("tab", { name: "Speicher" })).toHaveAttribute("aria-selected", "true");
    expect(window.location.search).toBe("?tab=storage");
    expect(screen.getByTestId("pool-local-lvm")).toBeVisible();
    fireEvent.click(screen.getByTestId("tile-updates"));
    expect(screen.getByRole("tab", { name: /^Updates/ })).toHaveAttribute("aria-selected", "true");
  });

  const quiet = (node: string, over: Record<string, unknown> = {}) => ({
    connection: "pve2", node, error: null, count: 0, badge: "aktuell", tone: "good", summary: "Auf dem neuesten Stand",
    last_check_ok: true, last_check_stale: false, packages: [], ...over,
  });
  function withData(updates: unknown, pools: unknown[] = []) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/ext/proxmox/updates")) return new Response(JSON.stringify(updates), { status: 200 });
      if (url.endsWith("/ext/proxmox/storage")) return new Response(JSON.stringify({ pools, errors: [] }), { status: 200 });
      return mockFetch()(input, init);
    }));
  }

  it("nichts zu tun: Updates „aktuell“, Speicher „keine Speicher gefunden“", async () => {
    withData({ nodes: [quiet("pve2"), quiet("pve3")], errors: [] });
    render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-updates")).toHaveTextContent("aktuell"));
    expect(screen.getByTestId("tile-storage")).toHaveTextContent("keine Speicher gefunden");
  });

  it("„aktuell“ nur, wenn die Prüfung frisch ist und jede Verbindung Knoten geliefert hat", async () => {
    withData({ nodes: [quiet("pve2"), quiet("pve3", { last_check_ok: false })], errors: [] });
    const { unmount } = render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-updates")).toHaveTextContent("Prüfung auf Updates veraltet oder fehlgeschlagen"));
    unmount();

    withData({ nodes: [quiet("pve2", { last_check_stale: true })], errors: [] });
    const second = render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-updates")).toHaveTextContent("?"));
    expect(screen.getByTestId("tile-updates")).not.toHaveTextContent("aktuell");
    second.unmount();

    // Verbindung mit Hosts, aber ohne Knoten und ohne Fehler (z. B. abgeschaltet): kein „aktuell“.
    withData({ nodes: [], errors: [] });
    render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-updates")).toHaveTextContent("nicht alle Knoten abrufbar"));
  });

  it("Speicher, den Proxmox als nicht aktiv meldet, steht vor jedem vollen", async () => {
    withData({ nodes: [quiet("pve2"), quiet("pve3")], errors: [] }, [
      { ...pool("local", "pve2", 95, "danger") },
      { ...pool("nas", "pve2", 0, "danger"), used_percent: null, total: null, used: null },
    ]);
    render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-storage")).toHaveTextContent("nas (pve2) nicht verfügbar"));
    expect(screen.getByTestId("tile-storage").querySelector(".text-red-300")).not.toBeNull();
  });

  it("Verbindung nicht erreichbar: nie „aktuell“ oder „keine Speicher“ behaupten", async () => {
    const dead = { errors: [{ connection: "pve2", error: "All connection attempts failed" }] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/ext/proxmox/updates")) return new Response(JSON.stringify({ nodes: [], ...dead }), { status: 200 });
      if (url.endsWith("/ext/proxmox/storage")) return new Response(JSON.stringify({ pools: [], ...dead }), { status: 200 });
      return mockFetch()(input, init);
    }));
    render(<ProxmoxNodePage />);
    await waitFor(() => expect(screen.getByTestId("tile-updates")).toHaveTextContent("nicht alle Knoten abrufbar"));
    expect(screen.getByTestId("tile-updates")).not.toHaveTextContent("aktuell");
    expect(screen.getByTestId("tile-storage")).toHaveTextContent("gerade nicht abrufbar");
  });
});

describe("Reiter", () => {
  it("Link aus der Speicher-Meldung (?tab=storage&host=): Reiter offen, Pools des Knotens markiert, dorthin gescrollt", async () => {
    const scrolled: string[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this.id); };
    window.history.replaceState({}, "", "/ext/proxmox/nodes?tab=storage&host=h-node");
    try {
      render(<ProxmoxNodePage />);
      await waitFor(() => expect(scrolled).toEqual(["storage-pve2"]));
      expect(screen.getByRole("tab", { name: "Speicher" })).toHaveAttribute("aria-selected", "true");
      const marked = screen.getAllByTestId(/^pool-/).filter((row) => row.className.includes("nodvard-deck-focus"));
      expect(marked.map((row) => row.querySelector(".font-medium")?.textContent)).toEqual(["local", "local-lvm"]);
      // Der Gast-Reiter markiert den Knoten dabei nicht: dort wäre er unsichtbar.
      expect(document.getElementById("host-h-node")?.className).not.toContain("nodvard-deck-focus");
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it("Knotenkarte: wartende Updates stehen dran, ein Klick führt in den Reiter", async () => {
    render(<ProxmoxNodePage />);
    await waitFor(() => expect(document.getElementById("host-h-node")).not.toBeNull());
    const badge = await within(document.getElementById("host-h-node")!).findByRole("button", { name: "3 Updates" });
    fireEvent.click(badge);
    expect(screen.getByRole("tab", { name: /^Updates/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("updates-pve2")).toBeVisible();
  });

  it("Reiterinhalt bleibt erhalten: ein aufgeklapptes Protokoll ist nach dem Wechsel noch da und wird nicht neu geladen", async () => {
    const task = {
      connection: "pve2", node: "pve2", upid: "UPID:pve2:9", type: "qmstart", type_label: "VM gestartet", guest_id: "100",
      guest_name: "docker", user: "root@pam", status: "OK", running: false, ok: true, starttime: 1790000300, duration_s: 4,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`GET ${url}`);
      if (url.includes("/ext/proxmox/tasks?")) return new Response(JSON.stringify({ tasks: [task] }), { status: 200 });
      if (url.includes("/log?upid=")) return new Response(JSON.stringify({ lines: ["starting VM 100", "TASK OK"] }), { status: 200 });
      return mockFetch()(input, init);
    }));
    render(<ProxmoxNodePage />);
    fireEvent.click(await screen.findByRole("tab", { name: "Aufgabenverlauf" }));
    fireEvent.click(await screen.findByTestId("task-UPID:pve2:9"));
    expect(await screen.findByText(/starting VM 100/)).toBeVisible();

    fireEvent.click(screen.getByRole("tab", { name: "Speicher" }));
    expect(screen.getByText(/starting VM 100/)).not.toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "Aufgabenverlauf" }));
    expect(screen.getByText(/starting VM 100/)).toBeVisible();
    expect(calls.filter((c) => c.includes("/log?upid=")).length).toBe(1);
  });

  it("unbekannter Reiter in der Adresse: „VMs & LXC“", async () => {
    window.history.replaceState({}, "", "/ext/proxmox/nodes?tab=gibtsnicht");
    render(<ProxmoxNodePage />);
    expect(await screen.findByRole("tab", { name: "VMs & LXC" })).toHaveAttribute("aria-selected", "true");
  });

  it("Tastatur: nur der offene Reiter ist ein Tab-Stopp, Pfeiltasten wechseln", async () => {
    render(<ProxmoxNodePage />);
    const first = await screen.findByRole("tab", { name: "VMs & LXC" });
    expect(screen.getAllByRole("tab").map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1", "-1", "-1", "-1"]);
    fireEvent.keyDown(first, { key: "ArrowRight" });
    const storage = screen.getByRole("tab", { name: "Speicher" });
    expect(storage).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(storage);
    fireEvent.keyDown(storage, { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "VMs & LXC" }), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Verbindungen" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Verbindungen" }), { key: "Home" });
    expect(screen.getByRole("tab", { name: "VMs & LXC" })).toHaveAttribute("aria-selected", "true");
  });

  it("Reiter „Verbindungen“: Verwaltung gleich offen, „Mit Passwort verbinden“ öffnet das Formular dort", async () => {
    render(<ProxmoxNodePage />);
    fireEvent.click(await screen.findByRole("tab", { name: "Verbindungen" }));
    const panel = document.getElementById("pve-panel-connections")!;
    expect(within(panel).getByRole("heading", { name: "Verbindungen verwalten" })).toBeVisible();
    fireEvent.click(await within(panel).findByRole("button", { name: "Mit Passwort verbinden" }));
    expect(within(panel).getByLabelText(/Adresse/)).toBeVisible();
  });

  it("ein Wechsel per Hand beendet den Sprung: der Knoten aus der Speicher-Meldung wird nicht aufgeklappt", async () => {
    window.history.replaceState({}, "", "/ext/proxmox/nodes?tab=storage&host=h-node");
    render(<ProxmoxNodePage />);
    await screen.findAllByTestId(/^pool-/);
    fireEvent.click(screen.getByRole("tab", { name: "VMs & LXC" }));
    expect(window.location.search).toBe("?tab=guests");
    expect(document.getElementById("host-h-node")?.className).not.toContain("nodvard-deck-focus");
    expect(within(document.getElementById("host-h-node")!).getByRole("button", { name: "Knotendetails anzeigen" })).toBeInTheDocument();
    expect(calls.some((c) => c.includes("/health"))).toBe(false);
  });

  it("ohne Proxmox-Server: keine Kacheln und keine Reiter, nur der Weg zum Verbinden", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/hosts?tag=proxmox")) return new Response("[]", { status: 200 });
      return mockFetch()(input, init);
    }));
    render(<ProxmoxNodePage />);
    expect(await screen.findByText("Noch keine Proxmox-Server gefunden")).toBeInTheDocument();
    expect(screen.queryByTestId("pve-summary")).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByText("Verbindungen verwalten")).toBeInTheDocument();
  });
});

describe("Aktionen einer VM", () => {
  it("während Neustarten läuft: Menüpunkt gesperrt mit Namen, „Mehr …“ zeigt, dass etwas läuft", async () => {
    let release: () => void = () => undefined;
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/actions/a1/approve")) {
        await new Promise<void>((resolve) => { release = resolve; });
      }
      return base(input, init);
    }));
    render(<ProxmoxNodePage />);
    const more = within((await screen.findByText("docker")).closest("tr")!).getByTestId("more-h-vm");
    fireEvent.click(within(more).getByText("Mehr"));
    fireEvent.click(within(more).getByRole("button", { name: "Neustarten" }));
    const busy = await within(more).findByRole("button", { name: "Neustarten (läuft …)" });
    expect(busy).toBeDisabled();
    expect(more.querySelector("summary")).toHaveTextContent("Mehr …");
    release();
    expect(await within(more).findByRole("button", { name: "Neustarten" })).toBeEnabled();
  });

  it("Neustarten steht unter „Mehr“: Rückfrage, Aktion, Menü zu", async () => {
    render(<ProxmoxNodePage />);
    const row = (await screen.findByText("docker")).closest("tr")!;
    const more = within(row).getByTestId("more-h-vm");
    fireEvent.click(within(more).getByText("Mehr"));
    expect(more).toHaveAttribute("open");
    fireEvent.click(within(more).getByRole("button", { name: "Neustarten" }));
    expect(more).not.toHaveAttribute("open");
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith('"docker" wirklich neu starten?', expect.anything()));
    await waitFor(() => expect(calls.some((c) => c.startsWith("POST ") && c.endsWith("/hosts/h-vm/actions/vm.reboot"))).toBe(true));
    expect(await screen.findByText("docker: Neustarten -> abgeschlossen.")).toBeInTheDocument();
  });
});
