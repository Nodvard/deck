import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { navigateTo } from "../../../_shared/frontend/src/testShell";

import { BackupsPage } from "./BackupsPage";

const JOBS = [
  { job_ref: "pve2--job1--100", connection: "pve2", vmid: "100", name: "docker", host_id: "h1", node: "pve2", storage: "backup-pve1", schedule: "0 2 * * *", enabled: true, last_status: "ok", last_run_at: 1700000000 },
];

/**
 * `POST .../retry` liefert bei autonomy.mode=propose (Default) IMMER
 * `status="proposed"` -- die alte Version dieses Tests pruefte nur, DASS der
 * Retry-Aufruf feuert, nie was danach passiert (die eigentliche Luecke: nichts
 * bestaetigte den Vorschlag je).
 */
function mockFetch(opts: { onRetry?: () => void; onApprove?: () => void; connections?: unknown[]; unprotected?: unknown; inventory?: unknown } = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/ext/backups/connections") && method === "GET") {
      return new Response(JSON.stringify(opts.connections ?? []), { status: 200 });
    }
    if (url.endsWith("/ext/backups/inventory")) {
      return new Response(JSON.stringify(opts.inventory ?? { guests: [], errors: [] }), { status: 200 });
    }
    if (url.endsWith("/ext/backups/unprotected")) {
      return new Response(JSON.stringify(opts.unprotected ?? { guests: [], errors: [] }), { status: 200 });
    }
    if (url.includes("/ext/backups/jobs") && !url.includes("/history") && !url.includes("/retry")) {
      return new Response(JSON.stringify(JOBS), { status: 200 });
    }
    if (url.includes("/retry") && method === "POST") {
      opts.onRetry?.();
      return new Response(JSON.stringify({ action_id: "a1", status: "proposed", risk: "medium" }), { status: 200 });
    }
    if (url.endsWith("/actions/a1/approve") && method === "POST") {
      opts.onApprove?.();
      return new Response(JSON.stringify({ id: "a1", status: "succeeded" }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
let hasPermission: Mock<(permission: string) => boolean>;

beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
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

describe("BackupsPage retry()", () => {
  it("sagt gleich oben, dass das Modul nur für Proxmox gedacht ist, und wohin die Sicherung von Nodvard Deck selbst gehört", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<BackupsPage />);
    const scope = await screen.findByTestId("backups-scope");
    expect(scope.textContent).toContain("Backup-Jobs deiner Proxmox-Server");
    expect(scope.textContent).toContain("Ohne Proxmox brauchst du dieses Modul nicht");
    expect(scope.textContent).toContain("Einstellungen → System");
  });

  it("fragt vor 'Erneut versuchen' ueber den echten Dialog-Mechanismus nach, nicht window.confirm()", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Erneut versuchen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("docker")));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/retry"), expect.anything()));
  });

  it("bricht ab, wenn die Rueckfrage abgelehnt wird", async () => {
    confirmDialog.mockResolvedValue(false);
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Erneut versuchen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(fetchMock).not.toHaveBeenCalledWith(expect.stringContaining("/retry"), expect.anything());
  });

  it("bestätigt einen vorgeschlagenen Retry automatisch, wenn der Nutzer die Berechtigung hat", async () => {
    let approved = false;
    vi.stubGlobal("fetch", mockFetch({ onApprove: () => (approved = true) }));
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Erneut versuchen" }));

    await waitFor(() => expect(approved).toBe(true));
    expect(hasPermission).toHaveBeenCalledWith("actions.approve:medium");
    await screen.findByText(/angenommen/);
  });

  it("bestätigt NICHT automatisch ohne die noetige Berechtigung -- verweist auf 'Aktionen'", async () => {
    hasPermission.mockReturnValue(false);
    let approveCalled = false;
    vi.stubGlobal("fetch", mockFetch({ onApprove: () => (approveCalled = true) }));
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Erneut versuchen" }));

    await screen.findByText(/Freigabe durch einen Admin nötig/);
    expect(approveCalled).toBe(false);
  });

  /** Live gefunden: ein grosses Backup fuellte den Backup-Speicher. Der Server warnt
   * (409 + space_warning), die Seite fragt nach und schickt erst bei "Trotzdem" erneut. */
  function mockSpaceWarning(retryBodies: unknown[]) {
    const base = mockFetch();
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/retry") && init?.method === "POST") {
        const body = JSON.parse(String(init.body ?? "{}"));
        retryBodies.push(body);
        if (!body.ignore_space) {
          return new Response(
            JSON.stringify({ detail: "'docker' hat bis zu 532 GB zu sichern, auf Speicher 'backup-pi' sind aber nur noch 146 GB frei.", space_warning: { vmid: "100" } }),
            { status: 409 },
          );
        }
      }
      return base(input, init);
    });
  }

  it("warnt, wenn das Backup nicht auf den Speicher passt, und sendet erst bei 'Trotzdem' erneut", async () => {
    const retryBodies: unknown[] = [];
    vi.stubGlobal("fetch", mockSpaceWarning(retryBodies));
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Erneut versuchen" }));

    await waitFor(() =>
      expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("nur noch 146 GB frei"), expect.objectContaining({ confirmLabel: "Trotzdem" })),
    );
    await screen.findByText(/angenommen/);
    expect(retryBodies).toEqual([{}, { ignore_space: true }]);
  });

  it("bricht nach der Platz-Warnung ab, wenn 'Trotzdem' abgelehnt wird", async () => {
    confirmDialog.mockImplementation(async (message: string) => !message.includes("Speicher reicht"));
    const retryBodies: unknown[] = [];
    vi.stubGlobal("fetch", mockSpaceWarning(retryBodies));
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Erneut versuchen" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(2));
    expect(retryBodies).toEqual([{}]);
    expect(screen.queryByText(/Fehler/)).toBeNull();
  });
});

/**
 * Verbindungen über API/UI verwalten -- identisches Muster wie ProxmoxNodePage.test.tsx, backups haelt
 * eine eigene, unabhaengige Verbindungsliste.
 */
describe("BackupsPage ConnectionsPanel", () => {
  it("legt eine neue Verbindung an und fordert danach ein Token an", async () => {
    let created: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/ext/backups/jobs") && !url.includes("/history") && !url.includes("/retry")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.endsWith("/ext/backups/connections") && method === "GET") {
        return new Response(JSON.stringify(created ? [created] : []), { status: 200 });
      }
      if (url.endsWith("/ext/backups/connections") && method === "POST") {
        created = { ...JSON.parse(init?.body as string), has_token: false };
        return new Response(JSON.stringify(created), { status: 201 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "+ Neue Verbindung" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "pve2" } });
    fireEvent.change(screen.getByLabelText("Adresse (URL)"), { target: { value: "https://192.168.1.23:8006" } });
    fireEvent.change(screen.getByLabelText("Token-ID"), { target: { value: "root@pam!dashboard" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await screen.findByText(/jetzt noch ein Token setzen/);
    expect((created as { name: string }).name).toBe("pve2");
  });

  it("schaltet eine Verbindung ueber den Aktiv-Knopf um (unabhaengiges Aktivieren/Deaktivieren)", async () => {
    let lastPatch: unknown = null;
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: true };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/ext/backups/jobs") && !url.includes("/history") && !url.includes("/retry")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.endsWith("/ext/backups/connections") && method === "GET") return new Response(JSON.stringify([conn]), { status: 200 });
      if (url.endsWith("/ext/backups/connections/pve2") && method === "PUT") {
        lastPatch = JSON.parse(init?.body as string);
        return new Response(JSON.stringify({ ...conn, enabled: false }), { status: 200 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "aktiv" }));

    await waitFor(() => expect(lastPatch).toEqual({ enabled: false }));
  });

  it("setzt und ersetzt ein Token über die Einstellungs-Schnittstelle (früher 409 beim zweiten Setzen)", async () => {
    const puts: { label: string; value: string }[] = [];
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: true };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/ext/backups/jobs") && !url.includes("/history") && !url.includes("/retry")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.endsWith("/ext/backups/connections") && method === "GET") return new Response(JSON.stringify([conn]), { status: 200 });
      if (url.endsWith("/extensions/backups/secrets") && method === "PUT") {
        puts.push(JSON.parse(init?.body as string));
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    (window.__lattice.promptDialog as ReturnType<typeof vi.fn>).mockResolvedValue("neues-token");
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Token ersetzen" }));

    await waitFor(() => expect(puts).toEqual([{ label: "backups-token:pve2", value: "neues-token" }]));
    await screen.findByText('Token für "pve2" gesetzt.');
  });

  it("fragt vor dem Entfernen einer Verbindung nach und entfernt sie danach", async () => {
    let deleted = false;
    const conn = { name: "pve2", base_url: "https://x:8006", token_id: "t", tls_insecure_skip_verify: false, enabled: true, has_token: true };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/ext/backups/jobs") && !url.includes("/history") && !url.includes("/retry")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.endsWith("/ext/backups/connections") && method === "GET") {
        return new Response(JSON.stringify(deleted ? [] : [conn]), { status: 200 });
      }
      if (url.endsWith("/ext/backups/connections/pve2") && method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<BackupsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Entfernen" }));

    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("pve2"), expect.anything()));
    await waitFor(() => expect(deleted).toBe(true));
  });

  it("zeigt Gäste ohne Backup-Job -- und nichts, wenn alles erfasst ist", async () => {
    vi.stubGlobal("fetch", mockFetch({
      unprotected: {
        guests: [
          { connection: "pve2", vmid: "100", name: "docker", host_id: "h1", kind: "VM" },
          { connection: "pve1", vmid: "104", name: "Teleport", host_id: null, kind: "Container" },
        ],
        errors: [{ connection: "alt", error: "nicht erreichbar" }],
      },
    }));
    const { unmount } = render(<BackupsPage />);
    const box = await screen.findByTestId("unprotected");
    expect(box.textContent).toContain("Ohne Backup-Job (2)");
    expect(box.textContent).toContain("docker · VM 100 · pve2");
    expect(box.textContent).toContain("Teleport · Container 104 · pve1");
    expect(box.textContent).toContain("alt: nicht prüfbar (nicht erreichbar)");
    unmount();

    vi.stubGlobal("fetch", mockFetch());
    render(<BackupsPage />);
    await screen.findByText("Backups");
    await waitFor(() => expect(screen.queryByTestId("unprotected")).toBeNull());
  });

  it("zeigt nächsten Lauf, Aufbewahrung und was wirklich auf den Speichern liegt -- auch für Gäste ohne Job", async () => {
    const jobs = [{ ...JOBS[0], next_run_at: 1790281800, retention: "letzte 3" }];
    const fetchMock = mockFetch({
      unprotected: { guests: [{ connection: "pve1", vmid: "104", name: "Teleport", host_id: null, kind: "Container" }], errors: [] },
      inventory: {
        guests: [
          { connection: "pve2", vmid: "100", count: 3, total_size: 11.5e9, newest_at: 1790209802, oldest_at: 1790123402, storages: ["backup-pve1"] },
          { connection: "pve1", vmid: "104", count: 1, total_size: 0.7e9, newest_at: 1788865818, oldest_at: 1788865818, storages: ["backup-pi"] },
        ],
        orphans: [{ connection: "pve2", vmid: "999", kind: "qemu", count: 2, total_size: 5e9, newest_at: 1788298941, oldest_at: 1788298900, storages: ["local"] }],
        errors: [],
      },
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/ext/backups/jobs")) return new Response(JSON.stringify(jobs), { status: 200 });
      return fetchMock(input, init);
    }));
    render(<BackupsPage />);

    const cell = await screen.findByTestId("inventory-pve2--job1--100");
    await waitFor(() => expect(cell.textContent).toContain("3× · neueste"));
    expect(cell.textContent).toContain("11.5 GB (backup-pve1)");
    expect(screen.getByText(/nächster:/)).toBeInTheDocument();
    expect(screen.getByText("behält: letzte 3")).toBeInTheDocument();
    const box = screen.getByTestId("unprotected");
    await waitFor(() => expect(box.textContent).toContain("1× · neueste"));
    expect(box.textContent).toContain("700 MB (backup-pi)");
    expect(screen.getByTestId("orphans").textContent).toContain("VM 999 (gesehen über pve2) · 2× · neueste");
  });

  it("'Bewusst so lassen' fragt nach einer Begründung, 'Wieder warnen' nimmt es zurück", async () => {
    let guests = [
      { connection: "pve2", vmid: "100", name: "docker", host_id: "h1", kind: "VM", acknowledged: false, note: null as string | null },
      { connection: "pve2", vmid: "110", name: "game-win", host_id: "h2", kind: "VM", acknowledged: false, note: null as string | null },
    ];
    const calls: { url: string; method: string; body?: unknown }[] = [];
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/acknowledged")) {
        calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
        guests = guests.map((g) => (url.includes(`/${g.vmid}/`) ? { ...g, acknowledged: method === "PUT", note: method === "PUT" ? "Risiko bewusst" : null } : g));
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/ext/backups/unprotected")) return new Response(JSON.stringify({ guests, errors: [] }), { status: 200 });
      return base(input, init);
    }));
    (window.__lattice.promptDialog as ReturnType<typeof vi.fn>).mockResolvedValue("Risiko bewusst");
    render(<BackupsPage />);

    const box = await screen.findByTestId("unprotected");
    expect(box.textContent).toContain("Ohne Backup-Job (2)");
    fireEvent.click(within(box).getAllByRole("button", { name: "Bewusst so lassen" })[0]);

    const accepted = await screen.findByTestId("unprotected-accepted");
    expect(accepted.textContent).toContain("docker");
    expect(accepted.textContent).toContain("„Risiko bewusst“");
    expect(screen.getByTestId("unprotected").textContent).toContain("Ohne Backup-Job (1)");
    expect(calls[0]).toEqual({ url: "/api/v1/ext/backups/unprotected/pve2/100/acknowledged", method: "PUT", body: { note: "Risiko bewusst" } });

    fireEvent.click(within(accepted).getByRole("button", { name: "Wieder warnen" }));
    await waitFor(() => expect(screen.queryByTestId("unprotected-accepted")).toBeNull());
    expect(calls[1].method).toBe("DELETE");
  });

  it("ohne settings.write keine Entscheidungs-Knöpfe", async () => {
    (window.__lattice.hasPermission as ReturnType<typeof vi.fn>).mockImplementation((p: string) => p !== "settings.write");
    vi.stubGlobal("fetch", mockFetch({
      unprotected: { guests: [{ connection: "pve2", vmid: "110", name: "game-win", host_id: null, kind: "VM" }], errors: [] },
    }));
    render(<BackupsPage />);
    const box = await screen.findByTestId("unprotected");
    expect(within(box).queryByRole("button", { name: "Bewusst so lassen" })).toBeNull();
  });

  it("Sprung von der Server-Seite (?host=): nur die Jobs dieses Hosts -- ehrlich, wenn keiner ihn erfasst", async () => {
    window.history.replaceState({}, "", "/ext/backups/backups?host=h1");
    try {
      vi.stubGlobal("fetch", mockFetch());
      const { unmount } = render(<BackupsPage />);
      expect((await screen.findByTestId("host-filter")).textContent).toContain("Nur docker");
      expect(screen.getByText("(100)")).toBeInTheDocument();
      unmount();

      window.history.replaceState({}, "", "/ext/backups/backups?host=h-ohne-job");
      render(<BackupsPage />);
      expect(await screen.findByText(/Kein Backup-Job erfasst diesen Host/)).toBeInTheDocument();
      expect(screen.queryByText("(100)")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
      expect(await screen.findByText("(100)")).toBeInTheDocument();
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("derselbe Link wirkt wieder, nachdem der Filter hier entfernt wurde", async () => {
    window.history.replaceState({}, "", "/ext/backups/backups?x=1&host=h-ohne-job");
    try {
      vi.stubGlobal("fetch", mockFetch());
      render(<BackupsPage />);
      expect(await screen.findByText(/Kein Backup-Job erfasst diesen Host/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
      expect(await screen.findByText("(100)")).toBeInTheDocument();
      expect(window.location.search).toBe("?x=1"); // nur ?host= entfernt

      // Für den Router ist das dieselbe Adresse wie beim Öffnen -- die Seite muss trotzdem filtern.
      navigateTo("/ext/backups/backups?x=1&host=h-ohne-job");
      expect(screen.getByTestId("host-filter")).toBeInTheDocument();
      expect(screen.queryByText("(100)")).toBeNull();

      navigateTo("/ext/backups/backups?host=h1");
      expect(screen.getByTestId("host-filter").textContent).toContain("Nur docker");
      expect(screen.getByText("(100)")).toBeInTheDocument();
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("Job bearbeiten: nur Änderungen, Warnung bei weniger Aufbewahrung, über das Gate", async () => {
    const posted: unknown[] = [];
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/jobs/pve2--job1--100/config")) {
        return new Response(JSON.stringify({ job_id: "job1", schedule: "02:00", enabled: true, storage: "backup-pve1", mode: "snapshot",
          "keep-last": 3, "keep-daily": 0, "keep-weekly": 2, "keep-monthly": 0, "keep-yearly": 0 }), { status: 200 });
      }
      if (url.includes("/jobs/pve2--job1--100/edit")) {
        posted.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ action_id: "e1", status: "proposed", risk: "high" }), { status: 200 });
      }
      if (url.endsWith("/actions/e1/approve")) {
        return new Response(JSON.stringify({ status: "succeeded", result: { output: "Geändert: Letzte 3 → 1." } }), { status: 200 });
      }
      return base(input, init);
    }));
    const confirm = vi.fn().mockResolvedValue(true);
    window.__lattice.confirmDialog = confirm;
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Job bearbeiten" }));
    const form = await screen.findByTestId("job-edit-pve2--job1--100");
    expect(form.textContent).toContain("Gilt für alle Gäste dieses Jobs: docker");
    fireEvent.change(within(form).getByLabelText("Letzte"), { target: { value: "1" } });
    expect(form.textContent).toContain("Weniger Aufbewahrung");
    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(posted).toEqual([{ changes: { "keep-last": 1 } }]));
    expect(confirm.mock.calls[0][1]).toMatchObject({ danger: true });
    expect(await screen.findByText("Geändert: Letzte 3 → 1.")).toBeInTheDocument();
  });

  /** Eine tote Verbindung (pve1 aus) versteckt nicht mehr alle anderen Jobs. */
  it("zeigt eine nicht erreichbare Verbindung als Hinweis und die übrigen Jobs normal", async () => {
    const jobs = [
      { job_ref: "", connection: "pve1", vmid: "", name: "Verbindung pve1", host_id: null, node: null, storage: "GET /cluster/backup -> All connection attempts failed",
        schedule: null, enabled: false, last_status: "unreachable", last_run_at: null, error: "GET /cluster/backup -> All connection attempts failed" },
      JOBS[0],
    ];
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/ext/backups/jobs")) return new Response(JSON.stringify(jobs), { status: 200 });
      return base(input, init);
    }));
    render(<BackupsPage />);

    const note = await screen.findByTestId("unreachable");
    expect(note.textContent).toContain("Verbindung „pve1“ nicht erreichbar");
    expect(note.textContent).toContain("All connection attempts failed");
    expect(screen.getAllByRole("button", { name: "Erneut versuchen" })).toHaveLength(1);
    expect(screen.getByText(/^1 Job\(s\)/)).toBeInTheDocument();
    expect(screen.getByText("(100)")).toBeInTheDocument();
  });

  it("zeigt beim Laden den Grund vom Server statt nur 'HTTP 409'", async () => {
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/ext/backups/jobs")) return new Response(JSON.stringify({ detail: "Keine Verbindung konfiguriert." }), { status: 409 });
      return base(input, init);
    }));
    render(<BackupsPage />);
    expect(await screen.findByText("Fehler: Keine Verbindung konfiguriert.")).toBeInTheDocument();
  });

  /** Aufbewahrung ehrlich zeigen -- "alle behalten" ist nur in Proxmox änderbar,
   * und wer statt der Speicher-Regel eine eigene setzt, wird gewarnt. */
  function mockJobConfig(config: Record<string, unknown>) {
    const base = mockFetch();
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/jobs/pve2--job1--100/config")) {
        return new Response(JSON.stringify({ job_id: "job1", schedule: "02:00", enabled: true, storage: "backup-pve1", mode: "snapshot",
          "keep-last": 0, "keep-daily": 0, "keep-weekly": 0, "keep-monthly": 0, "keep-yearly": 0, ...config }), { status: 200 });
      }
      return base(input, init);
    });
  }

  it("Job bearbeiten: 'alle behalten' sperrt die Aufbewahrung mit Hinweis", async () => {
    vi.stubGlobal("fetch", mockJobConfig({ "keep-all": true }));
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Job bearbeiten" }));
    const form = await screen.findByTestId("job-edit-pve2--job1--100");
    expect(form.textContent).toContain("Behält alle Sicherungen");
    expect(within(form).queryByLabelText("Letzte")).toBeNull();
    expect(within(form).getByLabelText("Zeitplan")).toBeInTheDocument();
  });

  it("Job bearbeiten: eigene Regel statt der des Speichers warnt", async () => {
    vi.stubGlobal("fetch", mockJobConfig({}));
    render(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Job bearbeiten" }));
    const form = await screen.findByTestId("job-edit-pve2--job1--100");
    expect(form.textContent).not.toContain("löscht Proxmox");
    fireEvent.change(within(form).getByLabelText("Letzte"), { target: { value: "2" } });
    expect(form.textContent).toContain("Bisher galt die Aufbewahrung des Speichers");
  });
});
