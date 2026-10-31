import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackupsPage } from "./BackupsPage";

/** Seiten-Durchgang: Ein Ladefehler ist keine Entwarnung ("keine Sicherung vorhanden", "Keine Läufe bekannt.",
 * kein Abschnitt "Ohne Backup-Job"). */

const JOB = {
  job_ref: "pve2--job1--100", connection: "pve2", vmid: "100", name: "docker", host_id: "h1", node: "pve2", storage: "backup-pve1",
  schedule: "0 2 * * *", enabled: true, last_status: "ok", last_run_at: 1700000000,
};
const INVENTORY = { guests: [{ connection: "pve2", vmid: "100", count: 2, newest_at: 1700000000, total_size: 3e9, storages: ["backup-pve1"] }], orphans: [] };
const UNPROTECTED = { guests: [{ connection: "pve2", vmid: "101", name: "zabbix", kind: "VM", acknowledged: false }], errors: [] };
const RUN = { upid: "UPID:pve2:1", node: "pve2", status: "ok", started_at: 1700000000, finished_at: 1700000600 };

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const fail = (detail: string) => new Response(JSON.stringify({ detail }), { status: 500 });

let broken: Map<string, string>;
let pendingHistory: ((r: Response) => void) | null;

beforeEach(() => {
  broken = new Map();
  pendingHistory = null;
  window.history.replaceState({}, "", "/ext/backups/backups");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    for (const [part, reason] of broken) if (url.includes(part)) return fail(reason);
    if (url.endsWith("/ext/backups/connections")) return ok([]);
    if (url.endsWith("/ext/backups/inventory")) return ok(INVENTORY);
    if (url.endsWith("/ext/backups/unprotected")) return ok(UNPROTECTED);
    if (url.includes("/history")) {
      if (broken.has("hold-history")) return new Promise<Response>((resolve) => { pendingHistory = resolve; });
      return ok([RUN]);
    }
    if (url.endsWith("/ext/backups/jobs")) return ok([JOB]);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("BackupsPage bei Ladefehlern", () => {
  it("Sicherungen nicht lesbar: „gerade nicht lesbar“ statt „keine Sicherung vorhanden“, erneut versuchen", async () => {
    broken.set("/ext/backups/inventory", "Speicher antwortet nicht.");
    render(<BackupsPage />);
    expect(await screen.findByText("Die Übersicht der vorhandenen Sicherungen ließ sich gerade nicht laden: Speicher antwortet nicht.")).toBeInTheDocument();
    expect(await screen.findByTestId(`inventory-${JOB.job_ref}`)).toHaveTextContent("gerade nicht lesbar");
    expect(screen.queryByText(/keine Sicherung vorhanden/)).toBeNull();

    broken.clear();
    const alert = screen.getByText(/Die Übersicht der vorhandenen Sicherungen/).closest("[data-testid='load-error']") as HTMLElement;
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId(`inventory-${JOB.job_ref}`)).toHaveTextContent("2× · neueste"));
    expect(screen.queryByText(/Die Übersicht der vorhandenen Sicherungen/)).toBeNull();
  });

  it("Gäste ohne Backup-Job nicht ladbar: der Abschnitt sagt es, statt still zu fehlen", async () => {
    broken.set("/ext/backups/unprotected", "Proxmox nicht erreichbar.");
    render(<BackupsPage />);
    const section = await screen.findByTestId("unprotected");
    expect(within(section).getByTestId("load-error")).toHaveTextContent(
      "Die Liste der Gäste ohne Backup-Job ließ sich gerade nicht laden: Proxmox nicht erreichbar.",
    );
    broken.clear();
    fireEvent.click(within(section).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Ohne Backup-Job (1)")).toBeInTheDocument();
  });

  it("Verlauf nicht ladbar: Grund statt „Keine Läufe bekannt.“, erneut versuchen", async () => {
    broken.set("/history", "Aufgabenliste nicht lesbar.");
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Verlauf" }));
    expect(await screen.findByText("Die Liste der Läufe ließ sich gerade nicht laden: Aufgabenliste nicht lesbar.")).toBeInTheDocument();
    expect(screen.queryByText("Keine Läufe bekannt.")).toBeNull();

    broken.clear();
    const alert = screen.getByText(/Die Liste der Läufe/).closest("[data-testid='load-error']") as HTMLElement;
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText(/– OK \(pve2\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Die Liste der Läufe/)).toBeNull();
  });

  it("solange der Verlauf lädt, steht „Lade …“ statt „Keine Läufe bekannt.“", async () => {
    broken.set("hold-history", "");
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Verlauf" }));
    expect(await screen.findByText("Lade …")).toBeInTheDocument();
    expect(screen.queryByText("Keine Läufe bekannt.")).toBeNull();
    pendingHistory?.(ok([]));
    expect(await screen.findByText("Keine Läufe bekannt.")).toBeInTheDocument();
  });
});
