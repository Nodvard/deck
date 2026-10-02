import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContainerWatch as SocPage } from "./ContainerWatch";

const STATS = {
  by_status: { open: 1, proposed: 1, reviewed: 0, resolved: 4, dismissed: 0 },
  pending_batch: 2, watched_hosts: 3, ai_healthy: true, ai_message: "ok",
};
const INCIDENTS = [
  {
    id: "inc-1", host_name: "docker", target: "nginx-proxy", message: "Container CRASH (Exited (1))",
    created_at: 1750000000, status: "proposed", status_label: "Aktion vorgeschlagen", status_changed_at: null,
    ai_summary: "Wiederholter Absturz durch OOM, Neustart vorgeschlagen.", action_id: "act-1", is_crash: true,
  },
];
const AUDIT = [
  { id: "a2", ts: "2026-09-24T07:05:00Z", actor_type: "extension", actor_id: "nexus-soc", action: "nexus_soc.incident_status", outcome: "success", reason: "Aktion vorgeschlagen -> Geprüft" },
  { id: "a1", ts: "2026-09-24T07:00:00Z", actor_type: "extension", actor_id: "nexus-soc", action: "nexus_soc.incident", outcome: "proposed", reason: "Container CRASH (Exited (1) 3 seconds ago)" },
];

function mockFetch(overrides: { incidents?: unknown[]; total?: number; stats?: unknown; hosts?: string[] } = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.includes("/ext/nexus-soc/history") && method === "GET") {
      const items = overrides.incidents ?? INCIDENTS;
      return new Response(
        JSON.stringify({ items, total: overrides.total ?? items.length, hosts: overrides.hosts ?? ["pi-host", "docker"] }),
        { status: 200 },
      );
    }
    if (url.endsWith("/ext/nexus-soc/stats") && method === "GET") {
      return new Response(JSON.stringify(overrides.stats ?? STATS), { status: 200 });
    }
    if (url.match(/\/incidents\/[^/]+\/(confirm|dismiss|resolve|reopen)$/) && method === "POST") {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    if (url.includes("/api/v1/audit?correlation_id=inc-1")) {
      return new Response(JSON.stringify(AUDIT), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

function historyCalls(fetchMock: ReturnType<typeof mockFetch>): URLSearchParams[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((u) => u.includes("/ext/nexus-soc/history"))
    .map((u) => new URL(u, "http://x").searchParams);
}

beforeEach(() => {
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("SocPage", () => {
  it("zeigt Status-Statistiken statt eines Chat-Eingabefelds", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);

    await screen.findByText("nginx-proxy", { exact: false });
    expect(screen.getByText("Beobachtete Hosts")).toBeInTheDocument();
    expect(screen.getByText("Wartet auf Bündelung")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Nachricht an NEXUS/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Senden" })).not.toBeInTheDocument();
  });

  it("zeigt Vorfaelle mit KI-Zusammenfassung und deutschen Badges", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);

    await screen.findByText(/nginx-proxy/);
    const row = within(screen.getByTestId("incident-inc-1"));
    expect(row.getByText(/Wiederholter Absturz durch OOM/)).toBeInTheDocument();
    expect(row.getByText("Aktion vorgeschlagen")).toBeInTheDocument();
    expect(row.getByText("Absturz")).toBeInTheDocument();
    expect(row.queryByText("Crash")).toBeNull();
  });

  it("zeigt, wie oft derselbe Vorfall aufgetreten ist", async () => {
    const repeated = { ...INCIDENTS[0], occurrences: 4, last_seen: 1750003600 };
    vi.stubGlobal("fetch", mockFetch({ incidents: [repeated] }));
    render(<SocPage />);

    await screen.findByText(/nginx-proxy/);
    const row = within(screen.getByTestId("incident-inc-1"));
    expect(row.getByText(/4× aufgetreten, zuletzt/)).toBeInTheDocument();
  });

  it("zeigt die KI-Erreichbarkeit an", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);

    await screen.findByText(/nginx-proxy/);
    expect(screen.getByText(/Nodvard KI: erreichbar/)).toBeInTheDocument();
  });

  it("ohne KI-Server: grauer Hinweis „optional“ statt einer roten Fehlermeldung", async () => {
    vi.stubGlobal("fetch", mockFetch({ stats: { ...STATS, ai_healthy: false, ai_message: "Kein Server für Nodvard KI eingetragen (optional).", ai_configured: false } }));
    render(<SocPage />);

    const note = await screen.findByTestId("ai-not-configured");
    expect(note.textContent).toContain("Nodvard KI ist nicht eingerichtet (optional)");
    expect(note.textContent).toContain("zeigt abgestürzte Container trotzdem an");
    expect(screen.queryByText(/Nodvard KI: /)).toBeNull();
  });

  it("KI eingetragen, aber nicht erreichbar: weiterhin die Anbindungs-Meldung", async () => {
    vi.stubGlobal("fetch", mockFetch({ stats: { ...STATS, ai_healthy: false, ai_message: "HTTP 502", ai_configured: true } }));
    render(<SocPage />);
    expect(await screen.findByText(/Nodvard KI: nicht erreichbar -- HTTP 502/)).toBeInTheDocument();
    expect(screen.queryByTestId("ai-not-configured")).toBeNull();
  });

  it.each([
    ["Bestätigen", "confirm"],
    ["Erledigt", "resolve"],
    ["Verwerfen", "dismiss"],
  ])("%s geht an den echten Endpunkt", async (label, verb) => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<SocPage />);

    await screen.findByText(/nginx-proxy/);
    fireEvent.click(within(screen.getByTestId("incident-inc-1")).getByRole("button", { name: label }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining(`/incidents/inc-1/${verb}`), expect.objectContaining({ method: "POST" })),
    );
  });

  it("abgeschlossene Vorfaelle lassen sich wieder oeffnen, aber nicht erneut bestaetigen", async () => {
    const fetchMock = mockFetch({ incidents: [{ ...INCIDENTS[0], status: "dismissed", status_label: "Verworfen" }] });
    vi.stubGlobal("fetch", fetchMock);
    render(<SocPage />);

    await screen.findByText(/nginx-proxy/);
    expect(screen.queryByRole("button", { name: "Bestätigen" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Wieder öffnen" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/incidents/inc-1/reopen"), expect.objectContaining({ method: "POST" })),
    );
  });

  it("durchsucht die Historie: Freitext, Status, Host und Zeitraum landen als Parameter", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<SocPage />);
    await screen.findByText(/nginx-proxy/);

    fireEvent.change(screen.getByLabelText("Historie durchsuchen"), { target: { value: "oom" } });
    fireEvent.click(screen.getByRole("button", { name: "Suchen" }));
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "resolved" } });
    fireEvent.change(screen.getByLabelText("Host-Filter"), { target: { value: "pi-host" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "2026-09-24" } });

    await waitFor(() => {
      const last = historyCalls(fetchMock).at(-1)!;
      expect(last.get("q")).toBe("oom");
      expect(last.get("status")).toBe("resolved");
      expect(last.get("host")).toBe("pi-host");
      expect(last.get("since")).toBe(new Date("2026-09-01T00:00:00").toISOString());
      expect(last.get("until")).toBe(new Date("2026-09-24T23:59:59.999").toISOString());
      expect(last.get("offset")).toBe("0");
    });

    fireEvent.click(screen.getByRole("button", { name: "Filter zurücksetzen" }));
    await waitFor(() => {
      const last = historyCalls(fetchMock).at(-1)!;
      expect(last.has("q")).toBe(false);
      expect(last.has("status")).toBe(false);
    });
  });

  it("blaettert seitenweise durch eine lange Historie", async () => {
    const fetchMock = mockFetch({ total: 60 });
    vi.stubGlobal("fetch", fetchMock);
    render(<SocPage />);

    expect(await screen.findByText("1–25 von 60 Vorfällen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ältere →" }));
    await waitFor(() => expect(historyCalls(fetchMock).at(-1)!.get("offset")).toBe("25"));
    expect(await screen.findByText("26–50 von 60 Vorfällen")).toBeInTheDocument();
  });

  it("zeigt den Verlauf eines Vorfalls aus dem Audit-Log, chronologisch", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    await screen.findByText(/nginx-proxy/);

    fireEvent.click(screen.getByRole("button", { name: "Verlauf" }));
    const trail = await screen.findByTestId("trail-inc-1");
    const items = within(trail).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toContain("Vorfall erfasst und von Nodvard KI bewertet");
    expect(items[0]).toContain("Container CRASH (Exited (1))");
    expect(items[0]).not.toContain("ago");
    expect(items[1]).toContain("Status geändert");
    expect(items[1]).toContain("Aktion vorgeschlagen -> Geprüft");
  });

  it("ohne audit.read kein Verlauf-Knopf", async () => {
    window.__lattice.hasPermission = vi.fn().mockReturnValue(false);
    vi.stubGlobal("fetch", mockFetch());
    render(<SocPage />);
    await screen.findByText(/nginx-proxy/);
    expect(screen.queryByRole("button", { name: "Verlauf" })).toBeNull();
  });

  it("zeigt einen Hinweis, wenn keine Vorfälle vorliegen", async () => {
    vi.stubGlobal("fetch", mockFetch({ incidents: [] }));
    render(<SocPage />);

    await screen.findByText("Keine Treffer.");
  });
});
