import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackupsPage } from "./BackupsPage";
import { FailureCause } from "./FailureCause";

const UPID_NEW = "UPID:pve2:0001A2B3:00C0FFEE:6703F1A0:vzdump:100:root@pam:";
const UPID_OLD = "UPID:pve2:0001A2B2:00C0FFED:6703F0A0:vzdump:100:root@pam:";

const CAUSE = {
  upid: UPID_NEW, node: "pve2", started_at: 1700000000, finished_at: 1700000300, status: "job errors", log_readable: true,
  key: "no_space", what: "Auf dem Sicherungsziel ist kein Platz mehr frei.",
  todo: "Alte Sicherungen auf dem Ziel löschen.", detail: "Backup of VM 100 failed - No space left on device",
};

const JOB = {
  job_ref: "pve2--job1--100", connection: "pve2", vmid: "100", name: "docker", host_id: "h1", node: "pve2",
  storage: "backup-pve1", schedule: "0 2 * * *", enabled: true, last_status: "failed", last_run_at: 1700000000,
};

function mockFetch(calls: string[], cause: Record<string, unknown> | ((url: string) => Response) = CAUSE) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url.includes("/failure")) {
      return typeof cause === "function" ? cause(url) : new Response(JSON.stringify(cause), { status: 200 });
    }
    if (url.includes("/history")) {
      return new Response(JSON.stringify([
        { upid: UPID_NEW, node: "pve2", status: "failed", started_at: 1700000000, finished_at: 1700000300 },
        { upid: "UPID:pve2:warn", node: "pve2", status: "ok", warnings: 2, started_at: 1699990000, finished_at: 1699990300 },
        { upid: UPID_OLD, node: "pve2", status: "failed", started_at: 1699900000, finished_at: 1699900300 },
      ]), { status: 200 });
    }
    if (url.endsWith("/ext/backups/jobs")) return new Response(JSON.stringify([JOB]), { status: 200 });
    if (url.endsWith("/ext/backups/connections")) return new Response("[]", { status: 200 });
    if (url.endsWith("/ext/backups/inventory") || url.endsWith("/ext/backups/unprotected")) {
      return new Response(JSON.stringify({ guests: [], errors: [] }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
  }));
}

beforeEach(() => {
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn(),
    promptDialog: vi.fn(),
    hasPermission: () => true,
  };
});

describe("FailureCause", () => {
  it("zeigt die zwei festen Sätze und die Zeile von Proxmox", async () => {
    const calls: string[] = [];
    mockFetch(calls);
    render(<FailureCause jobRef="pve2--job1--100" />);
    expect(await screen.findByText(/kein Platz mehr frei/)).toBeInTheDocument();
    expect(screen.getByText(/Alte Sicherungen auf dem Ziel löschen/)).toBeInTheDocument();
    expect(screen.getByTestId("failure-detail")).toHaveTextContent("No space left on device");
    expect(screen.queryByText(/Sys.Audit/)).toBeNull();
    expect(calls).toEqual(["/api/v1/ext/backups/jobs/pve2--job1--100/failure"]);
  });

  it("sagt, wenn das Protokoll nicht lesbar war, und meldet einen Fehler vom Server", async () => {
    mockFetch([], { ...CAUSE, log_readable: false });
    const { unmount } = render(<FailureCause jobRef="pve2--job1--100" upid={UPID_OLD} />);
    expect(await screen.findByText(/Sys.Audit/)).toBeInTheDocument();
    unmount();
    mockFetch([], () => new Response(JSON.stringify({ detail: "Kein fehlgeschlagener Lauf gefunden." }), { status: 404 }));
    render(<FailureCause jobRef="pve2--job1--100" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Kein fehlgeschlagener Lauf gefunden.");
  });
});

describe("BackupsPage: Verlauf eines fehlgeschlagenen Jobs", () => {
  it("zeigt beim Aufklappen den Grund, „Warum?“ je Fehlschlag und „OK, mit Warnungen“", async () => {
    const calls: string[] = [];
    mockFetch(calls);
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Verlauf" }));
    const cause = await screen.findByTestId("failure-cause");
    expect(await within(cause).findByText(/kein Platz mehr frei/)).toBeInTheDocument();
    expect(await screen.findByText(/OK, mit Warnungen/)).toBeInTheDocument();

    const why = screen.getAllByRole("button", { name: /^Warum ist der Lauf vom/ });
    expect(why).toHaveLength(2);
    fireEvent.click(why[1]);
    await screen.findByText(/kein Platz mehr frei/);
    expect(calls.filter((url) => url.includes("/failure"))).toEqual([
      "/api/v1/ext/backups/jobs/pve2--job1--100/failure",
      `/api/v1/ext/backups/jobs/pve2--job1--100/failure?upid=${encodeURIComponent(UPID_OLD)}`,
    ]);
    expect(why[1]).toHaveAttribute("aria-pressed", "true");
  });
});
