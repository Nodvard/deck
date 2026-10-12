import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { NetworkPage, type AddressCheckResult, type NpmStatus, type PiholeStatus } from "./NetworkPage";

const NOT_CONFIGURED_PIHOLE: PiholeStatus = {
  state: "not_configured",
  message: "Pi-hole ist noch nicht eingerichtet. Adresse und Passwort unter Einstellungen → Erweiterungen → Netzwerk eintragen.",
  url: null, summary: null, blocking: null,
};
const NOT_CONFIGURED_NPM: NpmStatus = {
  state: "not_configured",
  message: "Nginx Proxy Manager ist noch nicht eingerichtet.",
  url: null, hosts: [], certificates: [], summary: null,
};

const PIHOLE: PiholeStatus = {
  state: "ok", message: null, url: "http://pihole.test",
  summary: { queries_total: 20000, queries_blocked: 3000, percent_blocked: 15, domains_blocked: 250000, gravity_updated_at: null, clients_active: 9 },
  blocking: { status: "enabled", enabled: true, timer_s: null },
};

const CERT_OK = {
  id: 11, name: "cloud.home.example", domains: ["cloud.home.example"], provider: "letsencrypt", provider_label: "Let's Encrypt",
  expires_at: "2026-12-01T10:00:00+00:00", days_left: 60, status: "ok" as const, status_label: "gültig", days_text: "noch 60 Tage",
};
const CERT_WARN = {
  id: 12, name: "nas.home.example", domains: ["nas.home.example"], provider: "letsencrypt", provider_label: "Let's Encrypt",
  expires_at: "2026-10-04T08:00:00+00:00", days_left: 5, status: "warn" as const, status_label: "läuft bald ab", days_text: "noch 5 Tage",
};
const CERT_EXPIRED = {
  id: 13, name: "Altes Zertifikat", domains: ["alt.home.example"], provider: "other", provider_label: "eigenes Zertifikat",
  expires_at: "2026-09-26T00:00:00+00:00", days_left: -4, status: "expired" as const, status_label: "abgelaufen", days_text: "seit 3 Tagen abgelaufen",
};

const NPM: NpmStatus = {
  state: "ok", message: null, url: "http://npm.test:81",
  certificates: [CERT_EXPIRED, CERT_WARN, CERT_OK],
  hosts: [
    { id: 1, domains: ["cloud.home.example"], target: "http://192.168.1.85:8080", enabled: true, ssl_forced: true, certificate_id: 11, certificate: CERT_OK, nginx_online: true, nginx_error: null },
    { id: 2, domains: ["nas.home.example"], target: "https://192.168.1.10:5001", enabled: true, ssl_forced: true, certificate_id: 12, certificate: CERT_WARN, nginx_online: null, nginx_error: null },
    { id: 3, domains: ["alt.home.example"], target: "http://192.168.1.20:80", enabled: false, ssl_forced: false, certificate_id: 13, certificate: CERT_EXPIRED, nginx_online: null, nginx_error: null },
  ],
  summary: { hosts: 3, hosts_enabled: 2, certificates: 3, certificates_warn: 1, certificates_expired: 1 },
};

type Call = { url: string; method: string; body: unknown };

const NO_CHECKS: AddressCheckResult = { state: "ok", message: null, checks: [], problems: 0 };
const CHECKS: AddressCheckResult = {
  state: "ok", message: null, problems: 1,
  checks: [
    { proxy_id: 1, status: "running", tone: "good", text: "Führt zum Container nextcloud auf docker – läuft.", container: "nextcloud", docker_host: "docker", docker_host_id: "h1", can_start: false },
    { proxy_id: 2, status: "stopped", tone: "bad", text: "Führt zum Container immich_server auf docker – der ist gestoppt. Die Seite ist so vermutlich nicht erreichbar.", container: "immich_server", docker_host: "docker", docker_host_id: "h1", can_start: true },
    { proxy_id: 3, status: "unknown", tone: "neutral", text: "Das Ziel ist kein bekannter Docker-Server. Vielleicht ein Dienst außerhalb von Docker.", container: null, docker_host: null, docker_host_id: null, can_start: false },
  ],
};

function mockFetch(
  calls: Call[], data: { pihole: PiholeStatus; npm: NpmStatus; checks?: AddressCheckResult }, approveStatus = "succeeded", output = "Erledigt.",
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (url.endsWith("/ext/network/pihole")) return json(data.pihole);
    if (url.endsWith("/ext/network/npm")) return json(data.npm);
    if (url.endsWith("/ext/network/npm/address-check")) return json(data.checks ?? NO_CHECKS);
    if (url.endsWith("/hosts/h1/actions/container.start")) return json({ action_id: "a4", status: "proposed", risk: "low" });
    if (url.endsWith("/ext/network/pihole/pause")) return json({ action_id: "a1", status: "proposed", risk: "low" });
    if (url.endsWith("/ext/network/pihole/resume")) return json({ action_id: "a2", status: "proposed", risk: "low" });
    if (/\/ext\/network\/npm\/hosts\/\d+\/(enable|disable)$/.test(url)) return json({ action_id: "a3", status: "proposed", risk: "medium" });
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

describe("Netzwerk-Seite", () => {
  it("zeigt ohne Einstellungen freundliche Hinweise mit Link zu den Einstellungen", async () => {
    vi.stubGlobal("fetch", mockFetch([], { pihole: NOT_CONFIGURED_PIHOLE, npm: NOT_CONFIGURED_NPM }));
    render(<NetworkPage />);

    const pihole = within(await screen.findByTestId("pihole-not-configured"));
    expect(pihole.getByText(/Pi-hole ist noch nicht eingerichtet/)).toBeInTheDocument();
    expect(pihole.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/network");
    const npm = within(await screen.findByTestId("npm-not-configured"));
    expect(npm.getByText("Nginx Proxy Manager ist noch nicht eingerichtet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pausieren/ })).not.toBeInTheDocument();
  });

  it("ohne Admin-Recht steht statt des Links, wer es einrichten kann", async () => {
    permissions.delete("extensions.manage");
    vi.stubGlobal("fetch", mockFetch([], { pihole: NOT_CONFIGURED_PIHOLE, npm: NOT_CONFIGURED_NPM }));
    render(<NetworkPage />);
    const pihole = within(await screen.findByTestId("pihole-not-configured"));
    expect(pihole.queryByRole("link")).not.toBeInTheDocument();
    expect(pihole.getByText(/Ein Administrator kann das/)).toBeInTheDocument();
  });

  it("zeigt Pi-hole-Zahlen, Proxy-Hosts mit Ziel und hebt ablaufende Zertifikate hervor", async () => {
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM }));
    render(<NetworkPage />);

    const pihole = within(await screen.findByTestId("pihole"));
    expect(pihole.getByText("20.000")).toBeInTheDocument();
    expect(pihole.getByText("3.000")).toBeInTheDocument();
    expect(pihole.getByText("15,0 %")).toBeInTheDocument();
    expect(pihole.getByText("250.000")).toBeInTheDocument();
    expect(screen.getByText("Blockierung aktiv")).toBeInTheDocument();

    const nas = within(await screen.findByTestId("host-2"));
    expect(nas.getByText("→ https://192.168.1.10:5001")).toBeInTheDocument();
    expect(nas.getByText("läuft bald ab · noch 5 Tage")).toBeInTheDocument();
    expect(screen.getByTestId("host-2").className).toContain("bg-amber");
    expect(screen.getByTestId("host-3").className).toContain("bg-red");
    expect(screen.getByTestId("host-1").className).not.toContain("bg-");

    const urgent = within(screen.getByTestId("urgent-certificates"));
    expect(urgent.getByText("Altes Zertifikat")).toBeInTheDocument();
    expect(urgent.getByText("abgelaufen · seit 3 Tagen abgelaufen")).toBeInTheDocument();
    expect(urgent.queryByText("cloud.home.example")).not.toBeInTheDocument();
  });

  it("pausiert Pi-hole nach Rückfrage über das Gate und gibt selbst frei", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: PIHOLE, npm: NPM }, "succeeded", "Pi-hole-Blockierung für 15 Minuten pausiert."));
    render(<NetworkPage />);

    fireEvent.click(await screen.findByRole("button", { name: "15 Minuten pausieren" }));
    await waitFor(() => expect(screen.getByText("Pi-hole-Blockierung für 15 Minuten pausiert.")).toBeInTheDocument());
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("15 Minuten"), expect.objectContaining({ confirmLabel: "Pausieren" }));
    expect(calls.find((c) => c.url.endsWith("/pihole/pause"))?.body).toEqual({ minutes: 15 });
    expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true);
    expect(calls.filter((c) => c.url.endsWith("/ext/network/pihole")).length).toBeGreaterThanOrEqual(2);
  });

  it("bei abgebrochener Rückfrage passiert nichts", async () => {
    confirmDialog.mockResolvedValue(false);
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: PIHOLE, npm: NPM }));
    render(<NetworkPage />);
    fireEvent.click(await screen.findByRole("button", { name: "5 Minuten pausieren" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("setzt eine pausierte Blockierung fort", async () => {
    const calls: Call[] = [];
    const paused = { ...PIHOLE, blocking: { status: "disabled", enabled: false, timer_s: 600 } };
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: paused, npm: NPM }, "succeeded", "Pi-hole blockiert wieder."));
    render(<NetworkPage />);
    expect(await screen.findByText("pausiert – noch 10 Min.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Blockierung fortsetzen" }));
    await waitFor(() => expect(screen.getByText("Pi-hole blockiert wieder.")).toBeInTheDocument());
    expect(calls.some((c) => c.url.endsWith("/pihole/resume") && c.method === "POST")).toBe(true);
  });

  it("Ausschalten eines Proxy-Hosts warnt, dass die Seite offline geht, und wartet ohne Recht auf Freigabe", async () => {
    permissions.delete("actions.approve:medium");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: PIHOLE, npm: NPM }));
    render(<NetworkPage />);

    fireEvent.click(within(await screen.findByTestId("host-2")).getByRole("button", { name: "Ausschalten" }));
    await waitFor(() => expect(screen.getByText(/wartet auf Freigabe/)).toBeInTheDocument());
    expect(confirmDialog).toHaveBeenCalledWith(
      expect.stringContaining("nicht mehr erreichbar"),
      expect.objectContaining({ danger: true, confirmLabel: "Ausschalten" }),
    );
    expect(calls.some((c) => c.url.endsWith("/npm/hosts/2/disable") && c.method === "POST")).toBe(true);
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
  });

  it("schaltet einen ausgeschalteten Host wieder ein", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: PIHOLE, npm: NPM }, "succeeded", "„alt.home.example“ ist jetzt eingeschaltet."));
    render(<NetworkPage />);
    fireEvent.click(within(await screen.findByTestId("host-3")).getByRole("button", { name: "Einschalten" }));
    await waitFor(() => expect(screen.getByText("„alt.home.example“ ist jetzt eingeschaltet.")).toBeInTheDocument());
    expect(calls.some((c) => c.url.endsWith("/npm/hosts/3/enable"))).toBe(true);
    expect(calls.some((c) => c.url.endsWith("/actions/a3/approve"))).toBe(true);
  });

  it("zeigt einen Fehler der Ausführung verständlich an", async () => {
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM }, "failed"));
    render(<NetworkPage />);
    fireEvent.click(await screen.findByRole("button", { name: "60 Minuten pausieren" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Pi-hole ist nicht erreichbar."));
  });

  it("ohne Ausführungsrecht gibt es keine Knöpfe", async () => {
    permissions.delete("hosts.execute");
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM }));
    render(<NetworkPage />);
    await screen.findByTestId("host-2");
    expect(screen.queryByRole("button", { name: /pausieren/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ausschalten" })).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Meldung und erneut versuchen", async () => {
    const calls: Call[] = [];
    const down: PiholeStatus = { ...NOT_CONFIGURED_PIHOLE, state: "unreachable", message: "Pi-hole ist nicht erreichbar ([Errno 111] Connection refused)." };
    const npmDown: NpmStatus = { ...NOT_CONFIGURED_NPM, state: "auth_failed", message: "Nginx Proxy Manager hat die Anmeldung abgelehnt – bitte E-Mail-Adresse und Passwort prüfen." };
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: down, npm: npmDown }));
    render(<NetworkPage />);

    const pihole = within(await screen.findByTestId("pihole-problem"));
    expect(pihole.getByText(/Connection refused/)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.getByText("Anmeldung fehlgeschlagen")).toBeInTheDocument();
    const before = calls.filter((c) => c.url.endsWith("/ext/network/pihole")).length;
    fireEvent.click(pihole.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/ext/network/pihole")).length).toBe(before + 1));
    expect(within(screen.getByTestId("npm-problem")).getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/network");
  });
});

describe("Adress-Check", () => {
  it("zeigt je Proxy-Host, wohin er führt, und zählt gestoppte Ziele", async () => {
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM, checks: CHECKS }));
    render(<NetworkPage />);
    expect(await screen.findByTestId("address-2")).toHaveTextContent("Führt zum Container immich_server auf docker – der ist gestoppt.");
    expect(screen.getByTestId("address-1")).toHaveTextContent("nextcloud auf docker – läuft.");
    expect(screen.getByTestId("address-summary")).toHaveTextContent("Adress-Check: Eine Adresse führt zu einem gestoppten Container.");
    expect(within(screen.getByTestId("host-1")).queryByRole("button", { name: "Container starten" })).toBeNull();
  });

  it("startet den gestoppten Container über die Freigabe", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch(calls, { pihole: PIHOLE, npm: NPM, checks: CHECKS }));
    render(<NetworkPage />);
    fireEvent.click(within(await screen.findByTestId("host-2")).getByRole("button", { name: "Container starten" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/hosts/h1/actions/container.start"))).toBe(true));
    expect(confirmDialog).toHaveBeenCalledWith(
      "Container „immich_server“ auf docker starten? Dahinter liegt nas.home.example.",
      expect.objectContaining({ confirmLabel: "Starten" }),
    );
    const start = calls.find((c) => c.url.endsWith("/hosts/h1/actions/container.start"))!;
    expect(start.body).toEqual({ payload: { container: "immich_server" }, reason: "Über den Adress-Check der Netzwerk-Seite gestartet (nas.home.example)." });
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/npm/address-check")).length).toBe(2));
  });

  it("ohne Server-Recht gibt es keinen Start-Knopf", async () => {
    permissions.delete("hosts.execute");
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM, checks: CHECKS }));
    render(<NetworkPage />);
    expect(await screen.findByTestId("address-2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Container starten" })).toBeNull();
  });

  it("sagt, warum es keinen Check gibt, und meldet ein gutes Ergebnis", async () => {
    const off: AddressCheckResult = { state: "no_catalog", message: "Für den Adress-Check braucht es die Service-Matrix.", checks: [], problems: 0 };
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM, checks: off }));
    const { unmount } = render(<NetworkPage />);
    expect(await screen.findByTestId("address-summary")).toHaveTextContent("Adress-Check: Für den Adress-Check braucht es die Service-Matrix.");
    unmount();

    // Nichts zugeordnet: keine grüne Entwarnung.
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM }));
    const second = render(<NetworkPage />);
    await waitFor(() => expect(screen.getByTestId("address-summary")).toHaveTextContent("Adress-Check: Kein Ziel ließ sich einem Container zuordnen."));
    second.unmount();

    const good: AddressCheckResult = { ...CHECKS, problems: 0, checks: [CHECKS.checks[0], CHECKS.checks[2]] };
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM, checks: good }));
    render(<NetworkPage />);
    await waitFor(() => expect(screen.getByTestId("address-summary")).toHaveTextContent(
      "Adress-Check: Kein aktiver Proxy-Host führt sicher zu einem gestoppten Container (1 von 2 Zielen zugeordnet).",
    ));
    expect(screen.getByTestId("address-summary").className).toContain("text-emerald-300");
  });

  it("ein gestoppter Container hinter einem ausgeschalteten oder unsicheren Ziel nimmt die Entwarnung", async () => {
    const unsure: AddressCheckResult = {
      ...CHECKS, problems: 0, checks: [CHECKS.checks[0], { ...CHECKS.checks[1], tone: "neutral", can_start: false }],
    };
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM, checks: unsure }));
    render(<NetworkPage />);
    await waitFor(() => expect(screen.getByTestId("address-summary")).toHaveTextContent("1 gestoppt, aber Proxy-Host aus oder Zuordnung unsicher"));
    expect(screen.getByTestId("address-summary").className).not.toContain("text-emerald-300");
  });

  it("eine langsame ältere Antwort überschreibt nicht die neuere", async () => {
    let releaseFirst: (r: Response) => void = () => undefined;
    let checkCalls = 0;
    const base = mockFetch([], { pihole: PIHOLE, npm: NPM, checks: CHECKS });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/ext/network/npm/address-check") && ++checkCalls === 1) {
        return new Promise<Response>((resolve) => { releaseFirst = resolve; });
      }
      return base(input, init);
    }));
    render(<NetworkPage />);
    await waitFor(() => expect(checkCalls).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("address-summary")).toHaveTextContent("Eine Adresse führt zu einem gestoppten Container.");
    releaseFirst(new Response(JSON.stringify(NO_CHECKS), { status: 200 }));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("address-summary")).toHaveTextContent("Eine Adresse führt zu einem gestoppten Container.");
  });

  it("sind alle Docker-Server weg, ist das keine Entwarnung", async () => {
    const down: AddressCheckResult = {
      state: "ok", message: null, problems: 0,
      checks: [{ proxy_id: 1, status: "unreachable", tone: "warn", text: "docker ist gerade nicht erreichbar, das Ziel ließ sich nicht prüfen.", container: null, docker_host: "docker", docker_host_id: null, can_start: false }],
    };
    vi.stubGlobal("fetch", mockFetch([], { pihole: PIHOLE, npm: NPM, checks: down }));
    render(<NetworkPage />);
    await waitFor(() => expect(screen.getByTestId("address-summary")).toHaveTextContent("es ließ sich nichts prüfen"));
    expect(screen.getByTestId("address-summary").className).not.toContain("text-emerald-300");
  });
});
