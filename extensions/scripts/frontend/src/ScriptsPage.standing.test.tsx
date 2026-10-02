import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ScriptsPage, describeStandingHost } from "./ScriptsPage";

const STANDING: {
  active: boolean; problem: string | null; granted_by_label: string; granted_at: string;
  hosts: { id: string; name: string; account: string | null; address?: string | null; port?: number | null }[];
  new_hosts: string[];
} = {
  active: true, problem: null, granted_by_label: "nico", granted_at: "2026-10-01T10:00:00+00:00",
  hosts: [{ id: "h-pi", name: "Raspberry Pi", account: "root", address: "192.168.2.20" }], new_hosts: [],
};

const PREVIEW = [{ id: "h-pi", name: "Raspberry Pi", account: "root", address: "192.168.2.20" }];

const SCRIPT = {
  id: "audit", name: "Audit", description: "", content: "lynis\n", params_schema: {},
  target: { kind: "host", host_id: "h-pi" }, schedule: "0 1 * * *", enabled: true, job_id: "script-audit",
  fingerprint: "fp1", standing_approval: null as typeof STANDING | null,
  standing_preview: PREVIEW as typeof PREVIEW | null, targets_fingerprint: "tfp1" as string | null,
};

function mockFetch(
  calls: { url: string; method: string; body?: string }[], script = SCRIPT, grantStatus = 200, fresh = script,
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });
    if (url.endsWith("/ext/scripts/scripts")) return new Response(JSON.stringify([script]), { status: 200 });
    if (url.endsWith("/audit/standing-approval") && method === "POST") {
      if (grantStatus !== 200) {
        return new Response(JSON.stringify({ detail: "Die Zielserver haben sich inzwischen geändert – lade die Seite neu und prüfe die Liste noch einmal." }), { status: grantStatus });
      }
      return new Response(JSON.stringify({ ...script, standing_approval: STANDING }), { status: 200 });
    }
    if (url.endsWith("/ext/scripts/scripts/audit") && method === "GET") return new Response(JSON.stringify(fresh), { status: 200 });
    if (url.endsWith("/audit/standing-approval") && method === "DELETE") return new Response(null, { status: 204 });
    if (url.includes("/jobs")) return new Response(JSON.stringify([]), { status: 200 });
    if (url.endsWith("/audit/runs") || url.endsWith("/audit/history")) return new Response(JSON.stringify([]), { status: 200 });
    if (url.endsWith("/host-groups")) return new Response(JSON.stringify([]), { status: 200 });
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([{ id: "h-pi", name: "pi", display_name: "Raspberry Pi" }]), { status: 200 });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
let hasPermission: Mock<(permission: string) => boolean>;
beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  hasPermission = vi.fn<(permission: string) => boolean>().mockReturnValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission,
  };
});

describe("ScriptsPage Dauerfreigabe", () => {
  it("nennt einen ungewöhnlichen SSH-Port bei den freigegebenen Servern, den Standard nicht", () => {
    const host = { id: "h1", name: "bastel-pi", account: "root", address: "192.168.2.20" };
    expect(describeStandingHost({ ...host, port: 22 })).toBe("bastel-pi (als root, 192.168.2.20)");
    expect(describeStandingHost({ ...host, port: null })).toBe("bastel-pi (als root, 192.168.2.20)");
    expect(describeStandingHost({ ...host, port: 2222 })).toBe("bastel-pi (als root, 192.168.2.20, Port 2222)");
  });

  it("erteilt die Dauerfreigabe mit dem angezeigten Fingerabdruck und zeigt, dass sie gilt", async () => {
    const calls: { url: string; method: string; body?: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    const panel = await screen.findByTestId("standing-approval");
    expect(panel.textContent).toContain("Läuft nach Zeitplan ohne Freigabe, solange du das Skript nicht änderst");

    fireEvent.click(within(panel).getByRole("checkbox", { name: "Ohne Freigabe nach Zeitplan" }));
    const state = await screen.findByTestId("standing-state");
    expect(state.textContent).toContain("Dauerfreigabe gilt");
    expect(state.textContent).toContain("nico");
    expect(state.textContent).toContain("Raspberry Pi");
    expect(state.textContent).toContain("Raspberry Pi (als root, 192.168.2.20)");
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/standing-approval"));
    expect(JSON.parse(post!.body!)).toEqual({ fingerprint: "fp1", targets_fingerprint: "tfp1" });
    // Der Dialog nennt die Server mit Konto, bevor man bestätigt.
    expect(confirmDialog.mock.calls[0][0]).toContain("Gilt für: Raspberry Pi (als root, 192.168.2.20)");
  });

  it("zeigt vor dem Erteilen, für welche Server mit welchem Konto die Freigabe gelten würde", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    expect((await screen.findByTestId("standing-preview")).textContent).toContain(
      "Würde gelten für: Raspberry Pi (als root, 192.168.2.20)",
    );
  });

  it("holt nach „Zielserver geändert“ die neue Liste, damit der nächste Dialog sie zeigt", async () => {
    const calls: { url: string; method: string; body?: string }[] = [];
    const fresh = { ...SCRIPT, standing_preview: [{ ...PREVIEW[0], account: "deck" }], targets_fingerprint: "tfp2" };
    vi.stubGlobal("fetch", mockFetch(calls, SCRIPT, 409, fresh));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    fireEvent.click(within(await screen.findByTestId("standing-approval")).getByRole("checkbox"));
    expect(await screen.findByText(/Zielserver haben sich inzwischen geändert/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("standing-preview").textContent).toContain("Raspberry Pi (als deck, 192.168.2.20)"),
    );
    fireEvent.click(within(screen.getByTestId("standing-approval")).getByRole("checkbox"));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(2));
    expect(confirmDialog.mock.calls[1][0]).toContain("als deck");
    await waitFor(() => {
      const posts = calls.filter((c) => c.method === "POST" && c.url.endsWith("/standing-approval"));
      expect(posts).toHaveLength(2);
      expect(JSON.parse(posts[1].body!).targets_fingerprint).toBe("tfp2");
    });
  });

  it("„Freigabe zurückziehen“ entfernt sie", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { ...SCRIPT, standing_approval: STANDING }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    fireEvent.click(await screen.findByRole("button", { name: "Freigabe zurückziehen" }));
    await waitFor(() => expect(screen.queryByTestId("standing-state")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/audit/standing-approval"))).toBe(true);
  });

  it("ohne Owner-/Admin-Recht ist der Schalter gesperrt und es gibt keinen Knopf zum Zurückziehen", async () => {
    hasPermission.mockImplementation((p) => p !== "actions.standing_approval");
    vi.stubGlobal("fetch", mockFetch([], { ...SCRIPT, standing_approval: STANDING }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    const panel = await screen.findByTestId("standing-approval");
    expect(within(panel).getByRole("checkbox")).toBeDisabled();
    expect(within(panel).queryByRole("button", { name: "Freigabe zurückziehen" })).not.toBeInTheDocument();
  });

  it("warnt vor dem Speichern, dass eine Änderung die Freigabe aufhebt, und zeigt erloschene als solche", async () => {
    vi.stubGlobal("fetch", mockFetch([], { ...SCRIPT, standing_approval: STANDING }));
    const { unmount } = render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    fireEvent.change(await screen.findByLabelText("Skript-Inhalt"), { target: { value: "lynis --quick\n" } });
    expect((await screen.findByTestId("standing-state")).textContent).toContain("erlischt die Dauerfreigabe");
    unmount();

    vi.stubGlobal("fetch", mockFetch([], {
      ...SCRIPT, standing_approval: { ...STANDING, active: false, problem: "Auf „Raspberry Pi“ meldet sich das Dashboard jetzt unter einem anderen Konto an." },
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    const state = await screen.findByTestId("standing-state");
    expect(state.textContent).toContain("gilt nicht mehr");
    expect(state.textContent).toContain("anderen Konto");
  });

  it("zeigt „gilt nicht mehr“ statt „ohne Klick“, wenn die freigebende Person keine Rechte mehr hat", async () => {
    const problem = "Die Person, die die Dauerfreigabe erteilt hat, ist kein Owner oder Admin mehr und darf keine Dauerfreigaben mehr erteilen.";
    vi.stubGlobal("fetch", mockFetch([], { ...SCRIPT, standing_approval: { ...STANDING, active: false, problem } }));
    render(<ScriptsPage />);
    const entry = await screen.findByText("Audit");
    expect(within(entry.closest("button")!).queryByText("ohne Klick")).not.toBeInTheDocument();
    fireEvent.click(entry);
    const state = await screen.findByTestId("standing-state");
    expect(state.textContent).not.toContain("Dauerfreigabe gilt");
    expect(state.textContent).toContain(`Gilt nicht mehr: ${problem}`);
  });

  it("markiert Läufe ohne Klick in den Ausführungen", async () => {
    const fetchMock = mockFetch([]);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/audit/runs")) {
        return new Response(JSON.stringify([{
          action_id: "a1", status: "succeeded", host_id: "h-pi", host_name: "Raspberry Pi", proposed_by: "extension/scripts",
          proposed_by_label: "Skripte", created_at: "2026-10-02T01:00:00Z", finished_at: null, exit_code: 0, output: "ok",
          error: "", duration_ms: 10, standing_approval: true,
          reason: "Skript 'Audit' (audit) ausführen – ohne Klick, lief mit Dauerfreigabe vom 01.10.2026 durch nico",
        }]), { status: 200 });
      }
      return fetchMock(input, init);
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Audit"));
    const list = await screen.findByTestId("executions");
    const who = within(list).getByText(/ohne Klick \(Dauerfreigabe\)$/);
    expect(who.getAttribute("title")).toContain("durch nico");
  });
});
