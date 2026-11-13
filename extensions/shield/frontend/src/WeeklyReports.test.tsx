import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HostFilter } from "./api";
import { SocPage } from "./SocPage";
import { WeeklyReports } from "./WeeklyReports";

const FIRST = 1793602800;
const SECOND = FIRST - 7 * 86400;
const SUMMARY = { updates: 3, security_updates: 1, new_findings: 1, resolved_findings: 1, unknown_update_hosts: 1, unknown_security_update_hosts: 1 };
const metadata = (id = "w1", created_at = FIRST) => ({ id, created_at, period_start: created_at - 7 * 86400, period_end: created_at, host_count: 2, summary: SUMMARY });
const REPORT = {
  ...metadata(), certificates_note: "Zertifikatsabläufe sind in Shield noch nicht gespeichert.",
  hosts: [
    {
      host_id: "pi", host_name: "Raspberry Pi", updates: { open: 3, security: 1, checked_at: FIRST - 60, error: null },
      scan: { status: "infected", infected: 1, files_scanned: 1200, started_at: FIRST - 120, finished_at: FIRST - 60 },
      fail2ban: { status: "running", banned: 2, checked_at: FIRST - 60, error: null }, lynis: { hardening_index: 78, created_at: FIRST - 300 },
      findings: { new: [{ id: "f1", path: "/tmp/test.txt", signature: "Test.Signatur", status: "detected" }], resolved: [{ id: "f2", path: "/tmp/alt.txt", signature: "Alt.Signatur", status: "deleted" }] },
      certificates: null,
    },
    { host_id: "empty", host_name: "Ohne Messwerte", updates: null, scan: null, fail2ban: null, lynis: null, findings: { new: [], resolved: [] }, certificates: null },
  ],
};

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function fetchReports() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method ?? "GET").toBe("GET");
    const url = input.toString();
    if (url.endsWith("/reports")) return json({ items: [metadata(), metadata("w2", SECOND)], total: 2 });
    if (url.endsWith("/reports/w1")) return json(REPORT);
    if (url.endsWith("/reports/w2")) return json({ ...REPORT, ...metadata("w2", SECOND), hosts: [{ ...REPORT.hosts[0], host_name: "Alter Servername" }] });
    throw new Error(`Unerwarteter Abruf: ${url}`);
  });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/shield/soc?tab=reports");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", fetchReports());
});

describe("Wochenberichte", () => {
  it("zeigt gespeicherte Werte, Funde und unbekannte Daten ohne falsche Entwarnung", async () => {
    render(<WeeklyReports />);
    const pi = within(await screen.findByTestId("report-host-pi"));
    expect(pi.getByText("3 offen, davon 1 Sicherheitsupdate")).toBeInTheDocument();
    expect(pi.getByText(/Bedrohung gefunden: 1 Fund/)).toBeInTheDocument();
    expect(pi.getByText("2 gesperrte Adressen")).toBeInTheDocument();
    expect(pi.getByText("78 / 100")).toBeInTheDocument();
    expect(pi.getByText("/tmp/test.txt")).toBeInTheDocument();
    expect(pi.getByText("/tmp/alt.txt")).toBeInTheDocument();
    expect(pi.getByText("Keine gespeicherten Zertifikatsdaten")).toBeInTheDocument();
    const empty = within(screen.getByTestId("report-host-empty"));
    expect(empty.getByText("Keine gespeicherten Update-Daten")).toBeInTheDocument();
    expect(empty.getByText("Noch kein gespeicherter Virenscan")).toBeInTheDocument();
    expect(empty.getByText("Keine gespeicherten Fail2ban-Daten")).toBeInTheDocument();
    expect(empty.getByText("Noch kein erfolgreicher Härtungstest")).toBeInTheDocument();
    expect(screen.getByText(/Verlauf der letzten 12 Wochen/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zeitplan einstellen" })).toHaveAttribute("href", "/settings/extensions/shield");
  });

  it("wählt ältere Berichte und behält den Link beim erneuten Öffnen", async () => {
    const view = render(<WeeklyReports />);
    const older = (await screen.findAllByRole("button", { name: /Bericht vom/ }))[1];
    fireEvent.click(older);
    expect(await screen.findByText("Alter Servername")).toBeInTheDocument();
    expect(window.location.search).toBe("?tab=reports&report=w2");
    expect(older).toHaveAttribute("aria-pressed", "true");
    view.unmount();
    render(<WeeklyReports />);
    expect(await screen.findByText("Alter Servername")).toBeInTheDocument();
  });

  it("öffnet den Meldungslink in Shield ohne aktuelle Serverdaten abzurufen", async () => {
    window.history.replaceState(null, "", "/ext/shield/soc?tab=reports&report=w2");
    const fetch = fetchReports();
    vi.stubGlobal("fetch", fetch);
    render(<SocPage />);
    expect(screen.getByRole("tab", { name: "Wochenbericht" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Alter Servername")).toBeInTheDocument();
    expect(screen.queryByTestId("shield-more")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /Bericht vom/ })[0]);
    expect(await screen.findByTestId("report-host-pi")).toBeInTheDocument();
    expect(fetch.mock.calls.every(([url]) => url.toString().includes("/reports"))).toBe(true);
  });

  it("beachtet den Serverfilter", async () => {
    render(<HostFilter.Provider value="pi"><WeeklyReports /></HostFilter.Provider>);
    expect(await screen.findByTestId("report-host-pi")).toBeInTheDocument();
    expect(screen.queryByTestId("report-host-empty")).toBeNull();
  });

  it("zeigt nach fehlgeschlagenem Laden den Grund und kann erneut laden", async () => {
    const fetch = fetchReports().mockImplementationOnce(async () => json({ detail: "Datenbank nicht erreichbar" }, 503));
    vi.stubGlobal("fetch", fetch);
    render(<WeeklyReports />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Datenbank nicht erreichbar");
    expect(screen.queryByText("Noch kein Wochenbericht")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("report-host-pi")).toBeInTheDocument();
  });

  it("zeigt einen nicht mehr gespeicherten Bericht als Fehler mit Wiederholung", async () => {
    const fallback = fetchReports();
    let missing = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      input.toString().endsWith("/reports/w1") && missing ? json({ detail: "Bericht nicht gefunden" }, 404) : fallback(input, init)));
    render(<WeeklyReports />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Bericht nicht gefunden");
    missing = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("report-host-pi")).toBeInTheDocument();
  });

  it("unterscheidet bestätigte Nullwerte und nicht lesbare Sicherheitsupdates", async () => {
    const fallback = fetchReports();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => input.toString().endsWith("/reports/w1") ? json({ ...REPORT, hosts: [{
      ...REPORT.hosts[0], updates: { open: 0, security: null, checked_at: FIRST, error: "Sicherheitsupdates nicht lesbar" },
      scan: { ...REPORT.hosts[0].scan, status: "clean", infected: 0 }, fail2ban: { status: "running", banned: 0, checked_at: FIRST, error: null },
    }] }) : fallback(input, init)));
    render(<WeeklyReports />);
    const host = within(await screen.findByTestId("report-host-pi"));
    expect(host.getByText("0 offen, Sicherheitsupdates unbekannt")).toBeInTheDocument();
    expect(host.getByText("Sicherheitsupdates nicht lesbar")).toBeInTheDocument();
    expect(host.getByText("0 gesperrte Adressen")).toBeInTheDocument();
    expect(host.getByText(/Sauber: 0 Funde/)).toBeInTheDocument();
  });

  it("erklärt die leere Historie und verbirgt den Einstellungslink ohne Recht", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ items: [], total: 0 })));
    window.__nodvardDeck.hasPermission = vi.fn((permission) => permission === "settings.write");
    render(<WeeklyReports />);
    expect(await screen.findByText("Noch kein Wochenbericht")).toBeInTheDocument();
    expect(screen.getByText(/Der erste Bericht erscheint zum eingestellten Wochenzeitpunkt/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Zeitplan einstellen" })).toBeNull();
  });

  it("zeigt während des Ladens keinen leeren Verlauf", async () => {
    let resolve!: (r: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => { resolve = r; })));
    render(<WeeklyReports />);
    expect(screen.getByText("Lade Wochenberichte …")).toBeInTheDocument();
    expect(screen.queryByText("Noch kein Wochenbericht")).toBeNull();
    await act(async () => resolve(json({ items: [], total: 0 })));
    await waitFor(() => expect(screen.getByText("Noch kein Wochenbericht")).toBeInTheDocument());
  });
});
