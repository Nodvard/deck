import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { PiholePage, domainProblem, type PiholeStatus } from "./PiholePage";

const NOT_CONFIGURED: PiholeStatus = {
  state: "not_configured",
  message: "Pi-hole ist noch nicht eingerichtet. Adresse und Passwort unter Einstellungen → Erweiterungen → Pi-hole eintragen.",
  url: null, summary: null, blocking: null, top_clients: null,
};

const PIHOLE: PiholeStatus = {
  state: "ok", message: null, url: "http://pihole.test",
  summary: { queries_total: 20000, queries_blocked: 3000, percent_blocked: 15, domains_blocked: 250000, gravity_updated_at: null, clients_active: 9 },
  blocking: { status: "enabled", enabled: true, timer_s: null },
  top_clients: [
    { name: "fernseher.lan", ip: "192.0.2.21", count: 5400 },
    { name: "192.0.2.30", ip: "192.0.2.30", count: 1200 },
  ],
};

type Call = { url: string; method: string; body: unknown };

function mockFetch(calls: Call[], data: PiholeStatus, approveStatus = "succeeded", output = "Erledigt.") {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (url.endsWith("/ext/pihole/summary")) return json(data);
    if (/\/ext\/pihole\/(pause|resume|gravity)$/.test(url)) return json({ action_id: "a1", status: "proposed", risk: "low" });
    if (url.endsWith("/ext/pihole/domains")) return json({ action_id: "a2", status: "proposed", risk: "medium" });
    if (/\/actions\/a\d\/approve$/.test(url)) return json({ status: approveStatus, result: approveStatus === "succeeded" ? { output } : { error: "Pi-hole ist nicht erreichbar." } });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
let permissions: Set<string>;

beforeEach(() => {
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  permissions = new Set(["hosts.read", "hosts.execute", "actions.approve:low", "actions.approve:medium", "extensions.manage"]);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(),
    hasPermission: (p: string) => permissions.has(p),
  };
});

describe("Pi-hole-Seite", () => {
  it("zeigt ohne Einstellungen einen Hinweis mit Link zu den eigenen Einstellungen", async () => {
    vi.stubGlobal("fetch", mockFetch([], NOT_CONFIGURED));
    render(<PiholePage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByText(/Pi-hole ist noch nicht eingerichtet/)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/pihole");
    expect(screen.queryByRole("button", { name: /pausieren/ })).not.toBeInTheDocument();
  });

  it("zeigt Zahlen, aktivste Geräte und den Stand der Sperrlisten", async () => {
    vi.stubGlobal("fetch", mockFetch([], PIHOLE));
    render(<PiholePage />);
    const stats = within(await screen.findByTestId("stats"));
    expect(stats.getByText("20.000")).toBeInTheDocument();
    expect(stats.getByText("15,0 %")).toBeInTheDocument();
    expect(stats.getByText("9 aktive Geräte")).toBeInTheDocument();
    const clients = within(screen.getByTestId("top-clients"));
    expect(clients.getByText("fernseher.lan")).toBeInTheDocument();
    expect(clients.getByText("192.0.2.21")).toBeInTheDocument();
    expect(clients.getByText("5.400 Anfragen")).toBeInTheDocument();
    // Ohne Namen steht die Adresse nur einmal.
    expect(clients.getAllByText("192.0.2.30")).toHaveLength(1);
    expect(screen.getByText("250.000 Domains · aktualisiert unbekannt")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pi-hole öffnen" })).toHaveAttribute("href", "http://pihole.test/admin/");
  });

  it("pausiert nach Rückfrage über die Freigabe und zeigt das Ergebnis", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, PIHOLE, "succeeded", "Pi-hole-Blockierung für 15 Minuten pausiert."));
    render(<PiholePage />);
    fireEvent.click(await screen.findByRole("button", { name: "15 Minuten pausieren" }));
    expect(await screen.findByText("Pi-hole-Blockierung für 15 Minuten pausiert.")).toBeInTheDocument();
    expect(confirmDialog).toHaveBeenCalledTimes(1);
    const pause = calls.find((c) => c.url.endsWith("/ext/pihole/pause"));
    expect(pause?.method).toBe("POST");
    expect(pause?.body).toEqual({ minutes: 15 });
  });

  it("zeigt bei pausierter Blockierung die Restzeit und „fortsetzen“", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { ...PIHOLE, blocking: { status: "disabled", enabled: false, timer_s: 600 } }));
    render(<PiholePage />);
    const blocking = within(await screen.findByTestId("blocking"));
    expect(blocking.getByText("Blockierung pausiert, noch 10 Min.")).toBeInTheDocument();
    fireEvent.click(blocking.getByRole("button", { name: "Blockierung fortsetzen" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/ext/pihole/resume") && c.method === "POST")).toBe(true));
  });

  it("lädt die Sperrlisten nur nach Rückfrage neu", async () => {
    const calls: Call[] = [];
    confirmDialog.mockResolvedValueOnce(false);
    vi.stubGlobal("fetch", mockFetch(calls, PIHOLE));
    render(<PiholePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Listen aktualisieren" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1));
    expect(calls.some((c) => c.url.endsWith("/gravity"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Listen aktualisieren" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/ext/pihole/gravity") && c.method === "POST")).toBe(true));
  });

  it("erlaubt und sperrt Domains, prüft die Eingabe und leert das Feld danach", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, PIHOLE, "succeeded", "„werbung.example.com“ ist jetzt in Pi-hole gesperrt."));
    render(<PiholePage />);
    const form = within(await screen.findByTestId("domain-form"));
    const input = form.getByRole("textbox");
    fireEvent.change(input, { target: { value: "http://werbung.example.com/pfad" } });
    expect(form.getByText(/Bitte eine Domain wie/)).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Sperren" })).toBeDisabled();
    // Enter löst nichts aus.
    fireEvent.change(input, { target: { value: "Werbung.Example.com." } });
    fireEvent.submit(input);
    expect(confirmDialog).not.toHaveBeenCalled();
    fireEvent.click(form.getByRole("button", { name: "Sperren" }));
    expect(await screen.findByText("„werbung.example.com“ ist jetzt in Pi-hole gesperrt.")).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/ext/pihole/domains"))?.body).toEqual({ list: "deny", domain: "werbung.example.com" });
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("ohne Recht zum Steuern keine Knöpfe", async () => {
    permissions.delete("hosts.execute");
    vi.stubGlobal("fetch", mockFetch([], PIHOLE));
    render(<PiholePage />);
    await screen.findByTestId("stats");
    expect(screen.queryByRole("button", { name: /pausieren/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Listen aktualisieren" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("domain-form")).not.toBeInTheDocument();
  });

  it("zeigt Fehler mit „Erneut versuchen“", async () => {
    vi.stubGlobal("fetch", mockFetch([], { ...NOT_CONFIGURED, state: "auth_failed", message: "Pi-hole hat das Passwort abgelehnt." }));
    render(<PiholePage />);
    const box = within(await screen.findByTestId("problem"));
    expect(box.getByText("Pi-hole hat das Passwort abgelehnt.")).toBeInTheDocument();
    expect(box.getByRole("button", { name: "Erneut versuchen" })).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/pihole");
  });

  it("prüft Domains wie das Modul", () => {
    expect(domainProblem("")).toBeNull();
    expect(domainProblem("ads.example.com")).toBeNull();
    expect(domainProblem("Ads.Example.COM.")).toBeNull();
    for (const bad of ["localhost", "-a.example.com", "a..b", "a b.com", "*.example.com", "x".repeat(64) + ".com"]) {
      expect(domainProblem(bad)).not.toBeNull();
    }
  });
});
