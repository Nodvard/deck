import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { HostFilter } from "./api";
import { SecurityChecks } from "./SecurityChecks";

const HOST = {
  host_id: "server/1", host_name: "Heimserver", external_enabled: true,
  cve: {
    status: "ok", checked_at: 1794000000, counts: { high: 1, critical: 1 }, error: null,
    findings: [
      { package: "openssl", installed_version: "3.0.1", cve: "CVE-2026-1234", severity: "critical", fixed_version: "3.0.2" },
      { package: "curl", installed_version: "8.1", cve: "CVE-2026-5678", severity: "high", fixed_version: null },
    ],
  },
  external: {
    status: "warning", checked_at: 1794000000, open_ports: [22, 443, 8443], new_ports: [8443], warnings: [], error: null,
    certificates: [{
      hostname: "home.test", port: 443, status: "warning", issuer: "Home CA", expires_at: 1795000000,
      days_remaining: 12, hostname_matches: false, self_signed: true, trusted: false, protocol: "TLSv1.3",
      weak_protocols: ["TLSv1"], protocol_check_available: true, warnings: ["Zertifikat läuft bald ab."], error: null,
    }],
  },
};

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

afterEach(() => vi.useRealTimers());

beforeEach(() => {
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: () => true,
  };
});

it("zeigt CVEs und HTTPS-Befunde mit dem Update-Link für genau diesen Server", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ hosts: [HOST] })));
  render(<SecurityChecks canManage />);
  const host = within(await screen.findByTestId("checks-server/1"));
  expect(host.getByText("1 kritisch")).toBeInTheDocument();
  expect(host.getByText("1 hoch")).toBeInTheDocument();
  expect(host.getByText("openssl")).toBeInTheDocument();
  expect(host.getByText("CVE-2026-1234")).toBeInTheDocument();
  expect(host.getByText("Installiert: 3.0.1")).toBeInTheDocument();
  expect(host.getByText("Behoben ab: 3.0.2")).toBeInTheDocument();
  expect(host.getByText("Noch keine behobene Version bekannt.")).toBeInTheDocument();
  expect(host.getByRole("link", { name: "Updates ansehen" })).toHaveAttribute("href", "/ext/shield/soc?tab=updates&host=server%2F1");
  expect(host.getByText(/Offene TCP-Ports/).parentElement).toHaveTextContent("22, 443, 8443");
  expect(host.getByText("Neu erreichbar seit der letzten Prüfung: 8443")).toBeInTheDocument();
  expect(host.getByText("HTTPS: home.test:443")).toBeInTheDocument();
  expect(host.getByText("Home CA")).toBeInTheDocument();
  expect(host.getByText(/in 12 Tagen/)).toBeInTheDocument();
  expect(host.getByText("passt nicht zum Zertifikat")).toBeInTheDocument();
  expect(host.getByText(/Selbstsigniertes Zertifikat: Der Server bestätigt/)).toBeInTheDocument();
  expect(host.getByText("Schwache TLS-Protokolle: TLSv1")).toBeInTheDocument();
});

it("zeigt im Serverfilter nur den gewählten Server und keine Verwaltungsaktionen ohne Berechtigung", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ hosts: [HOST, { ...HOST, host_id: "other", host_name: "Zweiter Server" }] })));
  render(<HostFilter.Provider value="other"><SecurityChecks canManage={false} /></HostFilter.Provider>);
  await screen.findByTestId("checks-other");
  expect(screen.queryByTestId("checks-server/1")).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.queryByRole("button", { name: "Jetzt prüfen" })).toBeNull();
  expect(screen.getByRole("link", { name: "Updates ansehen" })).toHaveAttribute("href", "/ext/shield/soc?tab=updates&host=other");
});

it("startet genau eine lesende Prüfung und übernimmt ihr Ergebnis", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") return new Promise<Response>((resolve) => { finish = resolve; });
    return json({ hosts: [{ ...HOST, cve: null, external: null }] });
  });
  vi.stubGlobal("fetch", fetch);
  render(<SecurityChecks canManage />);
  await screen.findByTestId("checks-server/1");
  const button = screen.getByRole("button", { name: "Jetzt prüfen" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "Bitte warten …" })).toBeDisabled();
  expect(screen.getByRole("switch")).toBeDisabled();
  await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1));
  expect(fetch).toHaveBeenCalledWith("/api/v1/ext/shield/defender/checks/server%2F1/run", expect.objectContaining({ method: "POST" }));
  await act(async () => finish(json(HOST)));
  expect(await screen.findByText("CVE-2026-1234")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Jetzt prüfen" })).toBeEnabled();
});

it("schaltet nur die externe Prüfung aus und kennzeichnet das letzte Ergebnis", async () => {
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "PATCH" ? json({ ...HOST, external_enabled: false }) : json({ hosts: [HOST] }));
  vi.stubGlobal("fetch", fetch);
  render(<SecurityChecks canManage />);
  const toggle = await screen.findByRole("switch", { name: "Externe Prüfung für Heimserver" });
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(fetch).toHaveBeenCalledWith("/api/v1/ext/shield/defender/checks/server%2F1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ external_enabled: false }) }));
  expect(screen.getByText(/Das letzte Ergebnis bleibt zur Orientierung sichtbar/)).toBeInTheDocument();
  expect(screen.getByText(/Letztes Ergebnis:/)).toBeInTheDocument();
  expect(screen.getByText("CVE-2026-1234")).toBeInTheDocument();
});

it("holt das Ergebnis nach einer sofort bestätigten Hintergrundprüfung und beendet das Nachladen", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(json({ hosts: [{ ...HOST, cve: null, external: null }] }))
    .mockResolvedValueOnce(json({ ...HOST, cve: null, external: null, running: true }, 202))
    .mockResolvedValueOnce(json({ hosts: [{ ...HOST, running: false }] }));
  vi.stubGlobal("fetch", fetch);
  render(<SecurityChecks canManage />);
  const button = await screen.findByRole("button", { name: "Jetzt prüfen" });
  vi.useFakeTimers();
  await act(async () => fireEvent.click(button));
  expect(screen.getByRole("button", { name: "Prüfung läuft …" })).toBeDisabled();
  expect(screen.getByRole("switch")).toBeDisabled();
  await act(async () => vi.advanceTimersByTimeAsync(5000));
  expect(screen.getByText("CVE-2026-1234")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Jetzt prüfen" })).toBeEnabled();
  expect(fetch).toHaveBeenCalledTimes(3);
  await act(async () => vi.advanceTimersByTimeAsync(15_000));
  expect(fetch).toHaveBeenCalledTimes(3);
});

it.each(["error", "unsupported"])("gibt bei CVE-Status %s und fehlender externer Prüfung keine Entwarnung", async (status) => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ hosts: [{ ...HOST,
    cve: { ...HOST.cve, status, findings: [], counts: { high: 0, critical: 0 }, error: "Paketbestand nicht prüfbar" },
    external: { ...HOST.external, status: "unavailable", open_ports: [], new_ports: [], certificates: [], error: "Zeitüberschreitung" },
  }] })));
  render(<SecurityChecks canManage />);
  await screen.findByTestId("checks-server/1");
  expect(screen.getByText(/Paketbestand nicht prüfbar/)).toBeInTheDocument();
  expect(screen.getByText(/Externe Prüfung nicht abgeschlossen.*Zeitüberschreitung/)).toBeInTheDocument();
  expect(screen.queryByText("0 kritisch")).toBeNull();
  expect(screen.queryByText(/Keine bekannten Sicherheitslücken/)).toBeNull();
  expect(screen.queryByText(/Keine der geprüften Verbindungen/)).toBeNull();
});

it("kennzeichnet fehlende Protokollprüfung und unbekannte Zertifikatsfelder", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ hosts: [{ ...HOST, external: { ...HOST.external, certificates: [{
    ...HOST.external.certificates[0], issuer: null, expires_at: null, days_remaining: null, hostname_matches: null, self_signed: null,
    trusted: null, protocol: null, weak_protocols: [], protocol_check_available: false, warnings: [], error: "TLS nicht erreichbar",
  }] } }] })));
  render(<SecurityChecks canManage />);
  expect(await screen.findByText(/Zertifikat nicht vollständig geprüft: TLS nicht erreichbar/)).toBeInTheDocument();
  expect(screen.getByText("Schwache TLS-Protokolle konnten nicht vollständig geprüft werden.")).toBeInTheDocument();
  expect(screen.getAllByText("nicht geprüft")).toHaveLength(2);
  expect(screen.queryByText("passt zum Zertifikat")).toBeNull();
});

it("zeigt Ladefehler und kann die Prüfungen erneut laden", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ detail: "Dienst nicht erreichbar" }, 503)).mockImplementation(async () => json({ hosts: [HOST] }));
  vi.stubGlobal("fetch", fetch);
  render(<SecurityChecks canManage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Der Schutzstatus ist deshalb unbekannt.");
  fireEvent.click(screen.getByRole("button", { name: "Prüfungen erneut laden" }));
  await screen.findByTestId("checks-server/1");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("behält den Schalter bei einer abgelehnten Änderung und zeigt den Grund", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "PATCH"
    ? json({ detail: "Externe Prüfungen sind global ausgeschaltet." }, 409)
    : json({ hosts: [{ ...HOST, external_enabled: false }] })));
  render(<SecurityChecks canManage />);
  const toggle = await screen.findByRole("switch");
  fireEvent.click(toggle);
  expect(await screen.findByRole("alert")).toHaveTextContent("Externe Prüfungen sind global ausgeschaltet.");
  expect(toggle).not.toBeChecked();
  expect(toggle).toBeEnabled();
});

it("sagt, dass die Außenprüfung aus dem Heimnetz und nicht aus dem Internet prüft", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ hosts: [HOST] })));
  render(<SecurityChecks canManage={false} />);
  await screen.findByTestId("checks-server/1");
  expect(screen.getByText(/geprüft aus dem Heimnetz \(von Deck aus\), nicht aus dem Internet/)).toBeInTheDocument();
});
