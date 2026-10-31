import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { BackupsPage } from "./BackupsPage";
import { RestoreCopyPanel, type RestoreOptions } from "./RestoreCopy";

const GIB = 1024 ** 3;
const VOLID = "backup-nas:backup/vzdump-qemu-101-2026_10_01-02_00_00.vma.zst";

const BACKUPS = {
  vmid: "101", name: "web", kind: "qemu", exists: true, errors: [],
  backups: [
    { volid: VOLID, storage: "backup-nas", kind: "qemu", kind_label: "VM", ctime: 1790740800, size: 4 * GIB, notes: null, protected: false },
    { volid: "backup-nas:backup/vzdump-qemu-101-2026_09_01-02_00_00.vma.zst", storage: "backup-nas", kind: "qemu", kind_label: "VM", ctime: 1788148800, size: 4 * GIB, notes: "vor dem Update", protected: true },
  ],
};

const OPTIONS: RestoreOptions = {
  kind: "qemu", kind_label: "VM", source_vmid: "101", source_name: "web", source_exists: true,
  backup_at: 1790740800, backup_size: 4 * GIB, disk_bytes: 32 * GIB,
  nets: [{ key: "net0", bridge: "vmbr0", addresses: [] }],
  warnings: ["Bei einer VM steht die IP-Adresse meist im Gast-System selbst; Nodvard Deck sieht sie nicht. Die Kopie hat dieselbe wie das Original."],
  refuse: null, blocked: null, pool: "nodvard-kopien", suggested_vmid: 105, suggested_name: "web-kopie",
  keep_hint: "Willst du die Kopie behalten, nimm sie aus dem Pool nodvard-kopien: in Proxmox unter Rechenzentrum → Rechte → Pools → nodvard-kopien → Mitglieder die Kopie auswählen und „Entfernen“ klicken (das löscht sie nicht, sie verlässt nur den Pool).",
  nodes: [
    { node: "pve1", storages: [
      { storage: "local-lvm", type: "lvmthin", avail: 100 * GIB, need_bytes: 6.4 * GIB, fits: true, warning: null },
      { storage: "small", type: "lvmthin", avail: 3 * GIB, need_bytes: 6.4 * GIB, fits: false, warning: null },
    ] },
    { node: "pve2", storages: [
      { storage: "tank", type: "zfspool", avail: 20 * GIB, need_bytes: 6.4 * GIB, fits: true, warning: "Kann knapp werden: Die Platten der Kopie sind zusammen bis zu 32 GB groß, frei sind 20 GB." },
    ] },
  ],
  default_node: "pve1", default_storage: "local-lvm",
};

interface Call { url: string; method: string; body?: Record<string, unknown> }

function mockRestoreFetch(opts: { options?: RestoreOptions; restore?: () => Response; calls?: Call[] } = {}) {
  const calls = opts.calls ?? [];
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes("/ext/backups/restore/backups?")) return new Response(JSON.stringify(BACKUPS), { status: 200 });
    if (url.includes("/ext/backups/restore/options?")) return new Response(JSON.stringify(opts.options ?? OPTIONS), { status: 200 });
    if (url.endsWith("/ext/backups/restore") && method === "POST") {
      return opts.restore?.() ?? new Response(JSON.stringify({ action_id: "r1", status: "proposed", risk: "high", warnings: [] }), { status: 200 });
    }
    if (url.endsWith("/actions/r1/approve") && method === "POST") {
      return new Response(JSON.stringify({ id: "r1", status: "succeeded", result: { success: true, output: "Kopie angelegt: VM 105 „web-kopie“ auf pve1." } }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

let hasPermission: Mock<(permission: string) => boolean>;

beforeEach(() => {
  hasPermission = vi.fn<(permission: string) => boolean>().mockReturnValue(true);
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission,
  };
});

const TARGET = { connection: "pve", vmid: "101", name: "web" };

describe("Als Kopie wiederherstellen", () => {
  it("schlägt Nummer, Name, Knoten und passenden Speicher vor und nennt die festen Regeln samt Platz", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestoreFetch({ calls }));
    render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);

    expect(await screen.findByLabelText("Neue Nummer (VMID)")).toHaveValue("105");
    expect(screen.getByLabelText("Name der Kopie")).toHaveValue("web-kopie");
    expect(screen.getByLabelText("Zielknoten")).toHaveValue("pve1");
    expect(screen.getByLabelText("Zielspeicher")).toHaveValue("local-lvm");
    const small = within(screen.getByLabelText("Zielspeicher")).getByRole("option", { name: /small/ });
    expect(small).toBeDisabled();
    expect(small.textContent).toContain("zu wenig Platz");
    expect(screen.getByRole("checkbox")).toBeChecked();

    const rules = screen.getByTestId("restore-rules").textContent ?? "";
    expect(rules).toContain("Das Original bleibt unberührt");
    expect(rules).toContain("neue MAC-Adressen");
    expect(rules).toContain("startet nicht von selbst");
    expect(rules).toContain("feste IP-Adresse");
    expect(rules).toContain("„Getrennt“ abhaken");
    // Ehrlich, wann es passiert: erst wenn Proxmox fertig ist, sonst holt der Wächter es nach.
    expect(rules).toContain("sobald Proxmox mit der Wiederherstellung fertig ist");
    expect(rules).toContain("holt der Backup-Wächter das nach");
    expect(rules).toContain("Bis dahin die Kopie nicht starten");
    expect(rules).toContain("Die Kopie entsteht im Pool nodvard-kopien");
    expect(rules).toContain("Mitglieder die Kopie auswählen");
    expect(screen.getByTestId("restore-notes").textContent).toContain("im Gast-System");
    const space = screen.getByTestId("restore-space").textContent ?? "";
    expect(space).toContain("Platz reicht");
    expect(space).toContain("mindestens 6.4 GB");
    expect(space).toContain("bis zu 32 GB");
    expect(space).toContain("frei auf local-lvm: 100 GB");
    expect(calls[0].url).toBe("/api/v1/ext/backups/restore/backups?connection=pve&vmid=101");
    expect(calls[1].url).toBe(`/api/v1/ext/backups/restore/options?connection=pve&volid=${encodeURIComponent(VOLID)}`);
  });

  it("anderer Knoten: nur dessen Speicher, mit Warnung, wenn es knapp werden kann", async () => {
    vi.stubGlobal("fetch", mockRestoreFetch());
    render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Zielknoten"), { target: { value: "pve2" } });
    expect(screen.getByLabelText("Zielspeicher")).toHaveValue("tank");
    expect(screen.getByText(/Kann knapp werden/)).toBeInTheDocument();
  });

  it("schickt die gewählten Werte ans Gate und gibt mit actions.approve:high selbst frei", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestoreFetch({ calls }));
    const onDone = vi.fn();
    render(<RestoreCopyPanel target={TARGET} onDone={onDone} />);

    fireEvent.change(await screen.findByLabelText("Neue Nummer (VMID)"), { target: { value: "120" } });
    fireEvent.change(screen.getByLabelText("Name der Kopie"), { target: { value: "web-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Kopie anlegen" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith("Kopie angelegt: VM 105 „web-kopie“ auf pve1."));
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/ext/backups/restore"));
    expect(post?.body).toEqual({ connection: "pve", volid: VOLID, node: "pve1", storage: "local-lvm", vmid: 120, name: "web-test", link_down: true });
    expect(calls.some((c) => c.url.endsWith("/actions/r1/approve"))).toBe(true);
  });

  it("ohne Freigabe-Recht nur vorschlagen, und ohne getrennte Netzwerkkarten warnt der Hinweis", async () => {
    hasPermission.mockImplementation((p: string) => p !== "actions.approve:high");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestoreFetch({ calls }));
    const onDone = vi.fn();
    render(<RestoreCopyPanel target={TARGET} onDone={onDone} />);

    fireEvent.click(await screen.findByRole("checkbox"));
    expect(screen.getByTestId("restore-rules").textContent).toContain("Die Netzwerkkarten bleiben verbunden");
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    expect(screen.getByTestId("restore-summary").textContent).toContain("schlägst du die Wiederherstellung nur vor");
    fireEvent.click(screen.getByRole("button", { name: "Kopie anlegen" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith(expect.stringContaining("vorgeschlagen – Freigabe durch einen Admin nötig")));
    expect(calls.find((c) => c.method === "POST")?.body?.link_down).toBe(false);
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
  });

  it("zeigt die Ablehnung des Servers (z. B. Nummer inzwischen vergeben) und bleibt offen", async () => {
    vi.stubGlobal("fetch", mockRestoreFetch({
      restore: () => new Response(JSON.stringify({ detail: "Die Nummer 105 ist in Proxmox schon vergeben. Wähle eine freie Nummer." }), { status: 409 }),
    }));
    const onDone = vi.fn();
    render(<RestoreCopyPanel target={TARGET} onDone={onDone} />);
    fireEvent.click(await screen.findByRole("button", { name: "Weiter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Kopie anlegen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Die Nummer 105 ist in Proxmox schon vergeben");
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Neue Nummer (VMID)")).toBeInTheDocument();
  });

  it("ohne passenden Speicher oder bei einer Sicherung, die nur root wiederherstellen darf, gibt es keinen Knopf", async () => {
    const full = { ...OPTIONS, default_storage: null, nodes: [{ node: "pve1", storages: [OPTIONS.nodes[0].storages[1]] }] };
    vi.stubGlobal("fetch", mockRestoreFetch({ options: full }));
    const { unmount } = render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);
    expect(await screen.findByText("Auf diesem Knoten passt die Kopie auf keinen Speicher.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Weiter" })).toBeDisabled();
    unmount();

    vi.stubGlobal("fetch", mockRestoreFetch({ options: { ...OPTIONS, refuse: "Der Container bindet Ordner oder Geräte des Servers ein (mp0)." } }));
    render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);
    expect(await screen.findByText(/bindet Ordner oder Geräte/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Weiter" })).toBeNull();
  });

  it("zeigt vor dem Vorschlag eine Zusammenfassung; erst „Kopie anlegen“ geht an den Server", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestoreFetch({ calls }));
    const onDone = vi.fn();
    render(<RestoreCopyPanel target={TARGET} onDone={onDone} />);

    fireEvent.change(await screen.findByLabelText("Neue Nummer (VMID)"), { target: { value: "120" } });
    fireEvent.change(screen.getByLabelText("Name der Kopie"), { target: { value: "web-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));

    const summary = (await screen.findByTestId("restore-summary")).textContent ?? "";
    expect(summary).toContain("Neue Nummer: 120");
    expect(summary).toContain("Name: web-test");
    expect(summary).toContain("Knoten: pve1");
    expect(summary).toContain("Zielspeicher: local-lvm");
    expect(summary).toContain("Pool nodvard-kopien");
    expect(summary).toContain("Netzwerk getrennt");
    expect(summary).toContain("startet nicht von selbst");
    expect(summary).toContain("Das Original bleibt unberührt");
    expect(summary).toContain("Mitglieder die Kopie auswählen und „Entfernen“ klicken");
    expect(summary).toContain("gibst du die Wiederherstellung gleich selbst frei");
    // Bis hierhin ist nichts an die Freigabe gegangen, und die Eingaben sind weg (nichts mehr zu ändern).
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    expect(screen.queryByLabelText("Name der Kopie")).toBeNull();
    expect(screen.queryByRole("button", { name: "Weiter" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Kopie anlegen" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const posts = calls.filter((c) => c.method === "POST" && c.url.endsWith("/ext/backups/restore"));
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ connection: "pve", volid: VOLID, node: "pve1", storage: "local-lvm", vmid: 120, name: "web-test", link_down: true });
    expect(posts[0].body).not.toHaveProperty("pool"); // der Pool ist fest, der Server setzt ihn
  });

  it("„Zurück“ führt aus der Zusammenfassung zu den Eingaben, ohne dass etwas verloren geht oder gesendet wird", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestoreFetch({ calls }));
    render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Name der Kopie"), { target: { value: "web-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    fireEvent.click(await screen.findByRole("button", { name: "Zurück" }));
    expect(screen.getByLabelText("Name der Kopie")).toHaveValue("web-test");
    expect(screen.queryByTestId("restore-summary")).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("nennt in der Zusammenfassung „Netzwerk verbunden“, wenn die Netzwerkkarten angeschlossen bleiben", async () => {
    vi.stubGlobal("fetch", mockRestoreFetch());
    render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    const summary = (await screen.findByTestId("restore-summary")).textContent ?? "";
    expect(summary).toContain("Netzwerk verbunden");
    expect(summary).not.toContain("Netzwerk getrennt");
  });

  it("sperrt den Dialog mit dem Satz des Servers, wenn die Kopie nicht geht (zu alter Proxmox, Pool fehlt)", async () => {
    const sentence = "Eine VM als Kopie wiederherstellen braucht Proxmox VE 7.2 oder neuer. Dein Server hat 7.1-12. Spiel die Updates ein (Shield → Updates), dann geht es.";
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockRestoreFetch({ options: { ...OPTIONS, blocked: sentence }, calls }));
    render(<RestoreCopyPanel target={TARGET} onDone={vi.fn()} />);
    expect(await screen.findByTestId("restore-blocked")).toHaveTextContent(sentence);
    expect(screen.getByRole("button", { name: "Weiter" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Kopie anlegen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    expect(screen.queryByTestId("restore-summary")).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("Knopf auf der Backups-Seite", () => {
  const JOB = { job_ref: "pve--job1--101", connection: "pve", vmid: "101", name: "web", host_id: "h1", node: "pve1", storage: "backup-nas", schedule: "02:00", enabled: true, last_status: "ok", last_run_at: 1790740800 };

  function pageFetch(inventoryCount: number) {
    const restore = mockRestoreFetch();
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/ext/backups/connections")) return new Response("[]", { status: 200 });
      if (url.endsWith("/ext/backups/unprotected")) return new Response(JSON.stringify({ guests: [], errors: [] }), { status: 200 });
      if (url.endsWith("/ext/backups/jobs")) return new Response(JSON.stringify([JOB]), { status: 200 });
      if (url.endsWith("/ext/backups/inventory")) {
        return new Response(JSON.stringify({
          guests: [{ connection: "pve", vmid: "101", count: inventoryCount, total_size: 8 * GIB, newest_at: 1790740800, oldest_at: 1788148800, storages: ["backup-nas"] }],
          orphans: [],
        }), { status: 200 });
      }
      return restore(input, init);
    });
  }

  it("öffnet den Dialog unter der Zeile, wenn es Sicherungen gibt", async () => {
    vi.stubGlobal("fetch", pageFetch(2));
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Als Kopie wiederherstellen" }));
    const panel = await screen.findByRole("group", { name: "„web“ als Kopie wiederherstellen" });
    expect(await within(panel).findByLabelText("Neue Nummer (VMID)")).toHaveValue("105");
  });

  it("ohne Sicherung oder ohne hosts.execute kein Knopf", async () => {
    vi.stubGlobal("fetch", pageFetch(0));
    const { unmount } = render(<BackupsPage />);
    await screen.findByTestId("inventory-pve--job1--101");
    await waitFor(() => expect(screen.getByTestId("inventory-pve--job1--101").textContent).toContain("keine Sicherung"));
    expect(screen.queryByRole("button", { name: "Als Kopie wiederherstellen" })).toBeNull();
    unmount();

    hasPermission.mockImplementation((p: string) => p !== "hosts.execute");
    vi.stubGlobal("fetch", pageFetch(2));
    render(<BackupsPage />);
    await waitFor(() => expect(screen.getByTestId("inventory-pve--job1--101").textContent).toContain("2×"));
    expect(screen.queryByRole("button", { name: "Als Kopie wiederherstellen" })).toBeNull();
  });
});
