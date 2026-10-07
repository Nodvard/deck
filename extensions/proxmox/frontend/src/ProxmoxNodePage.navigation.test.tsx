/**
 * Ende-zu-Ende durch die echte Kette Router -> ExtensionPage (Kern) -> ProxmoxNodePage: die
 * Kachel "Aufgabenverlauf" (`?host=&tasks=1`) wirkt auch dann, wenn die Seite schon offen ist und
 * man den Filter zwischendurch entfernt hat. Vorbild: extensions/shield SocPage.navigation.test.tsx.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderInShell } from "../../../_shared/frontend/src/testShell";

vi.mock("../../../../frontend/src/lib/catalog", () => ({
  usePages: () => ({ data: [{ ext_id: "proxmox", path: "/nodes", component: "ProxmoxNodePage" }], isLoading: false }),
}));
// ExtensionPage laedt das Bundle per import(url) -- hier durch die echte Seite aus den Quellen ersetzt.
vi.mock("/api/v1/extensions/proxmox/frontend/index.js?v=dev", async () => ({ ProxmoxNodePage: (await import("./ProxmoxNodePage")).ProxmoxNodePage }));

const HOSTS = [
  { id: "h-node", name: "pve-pve2-pve2", display_name: "Proxmox-Knoten pve2", status: "up", kind: "hypervisor", provider_ref: "pve2/node/pve2" },
  { id: "h-vm", name: "docker", display_name: "docker", status: "down", kind: "vm", provider_ref: "pve2/qemu/pve2/100" },
];
const TASK = { connection: "pve2", node: "pve2", user: "root@pam", status: "OK", running: false, ok: true, endtime: null, duration_s: 4 };
const TASKS = [
  { ...TASK, upid: "UPID:pve2:2", type: "qmstart", type_label: "VM gestartet", guest_id: "100", guest_name: "docker", starttime: 1790000300 },
  { ...TASK, upid: "UPID:pve2:1", type: "startall", type_label: "Alle Gäste gestartet", guest_id: null, guest_name: null, starttime: 1790000100 },
];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (url.includes("/hosts?tag=proxmox")) return json(HOSTS);
    if (url.includes("/ext/proxmox/tasks?")) return json({ tasks: TASKS, errors: [] });
    if (url.endsWith("/ext/proxmox/storage")) return json({ pools: [], errors: [] });
    if (url.endsWith("/ext/proxmox/updates")) return json({ nodes: [], errors: [] });
    if (url.endsWith("/ext/proxmox/connections")) return json([]);
    // Aufgeklappter Gast (Metriken, Details, Snapshots): hier egal, die Felder zeigen nur einen Fehler.
    if (url.includes("h-vm")) return json({ detail: "egal" }, 404);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  });
}

const LINKS = { "Menü Proxmox": "/ext/proxmox/nodes", "Aufgabenverlauf docker": "/ext/proxmox/nodes?host=h-vm&tasks=1" };
const click = (name: string) => fireEvent.click(screen.getByRole("link", { name }));
const tasksShown = (n: number) => waitFor(() => expect(screen.getByText(`Aufgabenverlauf (${n})`)).toBeInTheDocument());

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/proxmox/nodes");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", mockFetch());
});
afterEach(async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("Proxmox-Knoten in der echten Kern-Shell", () => {
  it("Link, Filter entfernen, derselbe Link erneut: der Verlauf ist wieder nur für diesen Gast", async () => {
    renderInShell(LINKS);
    await tasksShown(2);

    click("Aufgabenverlauf docker");
    await tasksShown(1);

    fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
    await tasksShown(2);
    expect(window.location.search).toBe("?host=h-vm");

    // Der Router hält ?host=h-vm&tasks=1 noch für die aktuelle Adresse -- die Seite muss trotzdem folgen.
    click("Aufgabenverlauf docker");
    await tasksShown(1);
    expect(window.location.search).toBe("?host=h-vm&tasks=1");
    expect(screen.getByText("Aufgabenverlauf (1)").closest("details")).toHaveAttribute("open");
  });

  it("Zurück und Vor folgen", async () => {
    renderInShell(LINKS);
    await tasksShown(2);
    click("Aufgabenverlauf docker");
    await tasksShown(1);
    click("Menü Proxmox");
    await tasksShown(2);

    act(() => window.history.back());
    await tasksShown(1);
    act(() => window.history.forward());
    await tasksShown(2);
  });
});
