import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProxmoxNodePage } from "./ProxmoxNodePage";

/** Seiten-Durchgang: Scheitert die ganze Abfrage, steht der Grund da, statt dass Updates und Speicher still
 * fehlen und der Aufgabenverlauf "(0)" zeigt. */

const HOSTS = [
  { id: "h-node", name: "pve-pve2-pve2", display_name: "Proxmox-Knoten pve2", status: "up", kind: "hypervisor", provider_ref: "pve2/node/pve2" },
];
const TASK = {
  connection: "pve2", node: "pve2", upid: "UPID:pve2:3", type: "qmstart", type_label: "VM gestartet", guest_id: "100", guest_name: "docker",
  user: "root@pam", status: "OK", running: false, ok: true, starttime: 1790000300, endtime: 1790000304, duration_s: 4,
};

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const fail = (detail: string) => new Response(JSON.stringify({ detail }), { status: 500 });

let broken: Map<string, string>;

beforeEach(() => {
  broken = new Map();
  window.history.replaceState({}, "", "/ext/proxmox/nodes");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    for (const [part, reason] of broken) if (url.includes(part)) return fail(reason);
    if (url.includes("/hosts?tag=proxmox")) return ok(HOSTS);
    if (url.endsWith("/ext/proxmox/connections")) return ok([]);
    if (url.endsWith("/ext/proxmox/updates")) return ok({ nodes: [], errors: [] });
    if (url.endsWith("/ext/proxmox/storage")) return ok({ pools: [], errors: [] });
    if (url.includes("/ext/proxmox/tasks?")) return ok({ tasks: [TASK], errors: [] });
    // Kachel des Knotens (Auslastung, Gesundheit): hier egal.
    return new Response(JSON.stringify({ detail: "nicht da" }), { status: 404 });
  }));
});

function alertFor(text: RegExp): HTMLElement {
  return screen.getByText(text).closest("[data-testid='load-error']") as HTMLElement;
}

describe("ProxmoxNodePage bei Ladefehlern", () => {
  it("Updates, Speicher und Aufgabenverlauf: Grund statt still fehlender Abschnitte, erneut versuchen", async () => {
    broken.set("/ext/proxmox/updates", "Knoten antwortet nicht.");
    broken.set("/ext/proxmox/storage", "Speicherabfrage hängt.");
    broken.set("/ext/proxmox/tasks?", "Aufgaben nicht lesbar.");
    render(<ProxmoxNodePage />);

    expect(await screen.findByText("Die Liste der Updates ließ sich gerade nicht laden: Knoten antwortet nicht.")).toBeInTheDocument();
    expect(await screen.findByText("Die Liste der Speicher ließ sich gerade nicht laden: Speicherabfrage hängt.")).toBeInTheDocument();
    expect(await screen.findByText("Der Aufgabenverlauf ließ sich gerade nicht laden: Aufgaben nicht lesbar.")).toBeInTheDocument();
    expect(screen.queryByTestId("tasks-count-pve2")).toBeNull();
    // Der Aufgabenverlauf hat keine Kachel: das Warnzeichen am Reiter sagt, dass dort etwas nicht stimmt.
    expect(screen.getByRole("tab", { name: /^Aufgabenverlauf/ })).toHaveTextContent("Aufgabenverlauf!: nicht abrufbar");
    // Auch die Kacheln oben sagen es, statt „aktuell“ oder „keine Speicher“ zu behaupten.
    expect(screen.getByTestId("tile-updates")).toHaveTextContent("gerade nicht abrufbar");
    expect(screen.getByTestId("tile-storage")).toHaveTextContent("gerade nicht abrufbar");

    broken.clear();
    // Jeder Grund steht in seinem Reiter.
    for (const [tab, text] of [[/^Updates/, /Die Liste der Updates/], [/^Speicher/, /Die Liste der Speicher/], [/^Aufgabenverlauf/, /Der Aufgabenverlauf ließ/]] as const) {
      fireEvent.click(screen.getByRole("tab", { name: tab }));
      fireEvent.click(within(alertFor(text)).getByRole("button", { name: "Erneut versuchen" }));
    }
    await waitFor(() => expect(screen.queryByTestId("load-error")).toBeNull());
    expect(screen.getByTestId("tasks-count-pve2")).toHaveTextContent("1 Aufgabe");
    expect(screen.getByTestId("tile-updates")).not.toHaveTextContent("gerade nicht abrufbar");
    expect(screen.getByRole("tab", { name: "Aufgabenverlauf" })).toBeInTheDocument(); // Warnzeichen weg
  });

  it("Server-Liste: Grund vom Server statt „Fehler: HTTP 500“, erneut versuchen", async () => {
    broken.set("/hosts?tag=proxmox", "Datenbank gerade gesperrt.");
    render(<ProxmoxNodePage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Die Liste der Proxmox-Server ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    expect(screen.queryByText(/HTTP 500/)).toBeNull();
    broken.clear();
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Proxmox-Knoten pve2")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });
});
