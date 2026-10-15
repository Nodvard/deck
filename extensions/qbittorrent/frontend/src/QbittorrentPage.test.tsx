import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  QbittorrentPage, durationText, sizeText, speedText, torrentDetail,
  type Connection, type Overview, type Torrent, type TorrentCounts, type Transfer,
} from "./QbittorrentPage";

const MIB = 1024 * 1024;

const ubuntu: Torrent = {
  name: "ubuntu-24.04.iso", state: "downloading", progress_percent: 42, size: 6 * 1024 * MIB,
  download_speed: 3 * MIB, upload_speed: 256 * 1024, eta_seconds: 1250,
};
const debian: Torrent = {
  name: "debian-12.5.0-amd64-netinst.iso", state: "uploading", progress_percent: 100, size: 650 * MIB,
  download_speed: 0, upload_speed: 512 * 1024, eta_seconds: null,
};

const TRANSFER: Transfer = {
  download_speed: 4 * MIB + 100 * 1024, upload_speed: 800 * 1024, connection: "connected",
  download_limit: null, upload_limit: null, limited: false, alt_limits: false,
};

const COUNTS: TorrentCounts = { total: 14, downloading: 4, seeding: 3, paused: 2, error: 2, other: 3 };

const connection = (over: Partial<Connection> = {}): Connection => ({
  name: "qb",
  url: "http://qbt.example.com:8080",
  state: "ok",
  message: null,
  version: "v5.1.4",
  api_version: "2.11.4",
  transfer: TRANSFER,
  active: [ubuntu, debian],
  active_more: false,
  counts: COUNTS,
  free_space: 500 * 1024 * 1024 * 1024,
  notes: [],
  problems: [],
  ...over,
});

const failed = (over: Partial<Connection>): Connection =>
  connection({ version: null, api_version: null, transfer: null, active: null, counts: null, free_space: null, ...over });

const overviewOf = (...connections: Connection[]): Overview => ({
  connections,
  summary: {
    connections: connections.length,
    download_speed: connections.reduce((n, c) => n + (c.transfer?.download_speed ?? 0), 0),
    upload_speed: connections.reduce((n, c) => n + (c.transfer?.upload_speed ?? 0), 0),
    active: connections.reduce((n, c) => n + (c.active?.length ?? 0), 0),
  },
});

let permissions: Set<string>;
let urls: string[];
let answer: () => Response | Promise<Response>;

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

beforeEach(() => {
  permissions = new Set(["hosts.read", "extensions.manage"]);
  urls = [];
  answer = () => ok(overviewOf(connection()));
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(),
    hasPermission: (p: string) => permissions.has(p),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    urls.push(url);
    if (url.includes("/ext/qbittorrent/overview")) return answer();
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("qBittorrent-Seite", () => {
  it("zeigt Version, Geschwindigkeit, freien Platz, Zahl je Zustand und die aktiven Torrents", async () => {
    render(<QbittorrentPage />);
    const card = within(await screen.findByTestId("connection-qb"));

    expect(screen.getByText("2 Fehler", { selector: "span" })).toBeInTheDocument();
    expect(card.getByTestId("facts")).toHaveTextContent("Version v5.1.4 · Web-API 2.11.4");
    expect(screen.getByRole("link", { name: "qBittorrent öffnen" })).toHaveAttribute("href", "http://qbt.example.com:8080");

    const transfer = within(card.getByTestId("transfer"));
    expect(transfer.getByText("Herunterladen").closest("div")).toHaveTextContent("4,1 MB/s");
    expect(transfer.getByText("Hochladen").closest("div")).toHaveTextContent("800 KB/s");
    expect(transfer.getByText("Verbindung").closest("div")).toHaveTextContent("verbunden");
    expect(transfer.getByText("Freier Platz").closest("div")).toHaveTextContent("500 GB");
    expect(card.queryByTestId("limit")).not.toBeInTheDocument();

    expect(card.getByText("Torrents (14)")).toBeInTheDocument();
    const counts = within(card.getByTestId("counts"));
    expect(counts.getByText("Laden").closest("div")).toHaveTextContent("4");
    expect(counts.getByText("Verteilen").closest("div")).toHaveTextContent("3");
    expect(counts.getByText("Pausiert").closest("div")).toHaveTextContent("2");
    expect(counts.getByText("Fehler").closest("div")).toHaveTextContent("2");
    expect(counts.getByText("Warten oder prüfen").closest("div")).toHaveTextContent("3");

    expect(card.getByText("Aktiv (2)")).toBeInTheDocument();
    const rows = card.getAllByTestId("torrent");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("ubuntu-24.04.iso")).toBeInTheDocument();
    expect(within(rows[0]).getByText("42 % · ↓ 3,0 MB/s · ↑ 256 KB/s · noch 20 Min. · 6,0 GB")).toBeInTheDocument();
    expect(within(rows[0]).getByText("lädt")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(within(rows[1]).getByText("debian-12.5.0-amd64-netinst.iso")).toBeInTheDocument();
    expect(within(rows[1]).getByText("100 % · ↑ 512 KB/s · 650 MB")).toBeInTheDocument();
    expect(within(rows[1]).getByText("verteilt")).toBeInTheDocument();
    expect(urls).toEqual(["/api/v1/ext/qbittorrent/overview"]);
  });

  it("ohne Fehler und mit Bewegung heißt es „aktiv“", async () => {
    answer = () => ok(overviewOf(connection({ counts: { ...COUNTS, error: 0 } })));
    render(<QbittorrentPage />);
    expect(await screen.findByText("aktiv", { selector: "span" })).toBeInTheDocument();
  });

  it("ohne bewegte Torrents steht das da, die Zahlen bleiben sichtbar", async () => {
    const counts = { ...COUNTS, error: 0 };
    answer = () => ok(overviewOf(connection({ counts, active: [], transfer: { ...TRANSFER, download_speed: 0, upload_speed: 0 } })));
    render(<QbittorrentPage />);
    expect(await screen.findByText("ruhig", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByTestId("no-active")).toHaveTextContent("Gerade bewegt sich nichts.");
    expect(screen.queryByTestId("active")).not.toBeInTheDocument();
    expect(screen.getByTestId("counts")).toBeInTheDocument();
  });

  it("eine leere Bibliothek sagt es ausdrücklich", async () => {
    answer = () => ok(overviewOf(connection({ active: [], counts: { ...COUNTS, total: 0, downloading: 0, seeding: 0, paused: 0, error: 0, other: 0 } })));
    render(<QbittorrentPage />);
    expect(await screen.findByTestId("no-torrents")).toHaveTextContent("Noch keine Torrents.");
    expect(screen.queryByTestId("counts")).not.toBeInTheDocument();
  });

  it("Fehler, getrennt und Firewall fallen auf", async () => {
    answer = () => ok(overviewOf(
      connection({ name: "mit-fehler" }),
      connection({ name: "getrennt", transfer: { ...TRANSFER, connection: "disconnected" } }),
      connection({ name: "firewall", counts: { ...COUNTS, error: 0 }, transfer: { ...TRANSFER, connection: "firewalled" } }),
    ));
    render(<QbittorrentPage />);
    const errors = within(await screen.findByTestId("connection-mit-fehler"));
    expect(errors.getByText("Fehler", { selector: "p" }).closest("div")).toHaveTextContent("2");
    expect(screen.getByText("2 Fehler", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("getrennt", { selector: "span" })).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-firewall")).getByText("hinter Firewall")).toBeInTheDocument();
  });

  it("ein Tempolimit steht dabei, auch das alternative", async () => {
    answer = () => ok(overviewOf(connection({ transfer: { ...TRANSFER, limited: true, download_limit: MIB, alt_limits: true } })));
    render(<QbittorrentPage />);
    expect(await screen.findByTestId("limit")).toHaveTextContent("Alternative Tempolimits an: Download bis 1,0 MB/s");
  });

  it("ist der freie Platz unbekannt, steht das da und der Hinweis dazu", async () => {
    const note = "Anzahl je Zustand und freier Platz sind unbekannt: Die Liste aller Torrents ist größer als 8 MB und wird nicht gelesen.";
    answer = () => ok(overviewOf(connection({ counts: null, free_space: null, notes: [note] })));
    render(<QbittorrentPage />);
    const transfer = within(await screen.findByTestId("transfer"));
    expect(transfer.getByText("Freier Platz").closest("div")).toHaveTextContent("unbekannt");
    expect(screen.getByTestId("notes")).toHaveTextContent(note);
    expect(screen.queryByTestId("counts")).not.toBeInTheDocument();
    expect(screen.queryByText("unvollständig")).not.toBeInTheDocument();
    expect(screen.getByTestId("active")).toBeInTheDocument();
  });

  it("eine lange Liste sagt, dass es noch mehr gibt", async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ ...ubuntu, name: `Torrent ${i}` }));
    answer = () => ok(overviewOf(connection({ active: many, active_more: true })));
    render(<QbittorrentPage />);
    expect(await screen.findByText("Aktiv (50+)")).toBeInTheDocument();
    expect(screen.getAllByTestId("torrent")).toHaveLength(50);
    expect(screen.getByText("Es sind noch mehr aktiv (hier werden die ersten 50 gezeigt).")).toBeInTheDocument();
  });

  it("Namen aus qBittorrent erscheinen nur als Text, nie als HTML", async () => {
    const evil = { ...ubuntu, name: "<img src=x onerror=alert(1)>" };
    answer = () => ok(overviewOf(connection({ active: [evil] })));
    const { container } = render(<QbittorrentPage />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("jeder Zustand hat einen deutschen Namen, ein unbekannter keinen Zettel", async () => {
    const states = ["stalled_downloading", "stalled_uploading", "paused", "queued", "checking", "moving", "error", "etwas_neues"];
    answer = () => ok(overviewOf(connection({ active: states.map((state, i) => ({ ...ubuntu, name: `T${i}`, state })) })));
    render(<QbittorrentPage />);
    const rows = await screen.findAllByTestId("torrent");
    expect(rows.map((r) => within(r).queryAllByText(/^(wartet auf Quellen|verteilt|pausiert|wartet|prüft|verschiebt|Fehler)$/).map((e) => e.textContent)[0] ?? null)).toEqual([
      "wartet auf Quellen", "verteilt", "pausiert", "wartet", "prüft", "verschiebt", "Fehler", null,
    ]);
  });

  it("scheitert nur ein Abschnitt, bleiben die anderen stehen und der Grund steht dabei", async () => {
    const problem = "Anzahl und freier Platz: qBittorrent „qb“ meldet einen Fehler (HTTP 500). Schau in das Protokoll von qBittorrent.";
    answer = () => ok(overviewOf(connection({ counts: null, free_space: null, problems: [problem] })));
    render(<QbittorrentPage />);
    expect(await screen.findByTestId("problems")).toHaveTextContent(problem);
    expect(screen.getByText("unvollständig", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByTestId("transfer")).toBeInTheDocument();
    expect(screen.getByTestId("active")).toBeInTheDocument();
  });

  it("ohne Eintrag: Hinweis mit Link zu den Einstellungen, ohne Recht der Verweis auf einen Administrator", async () => {
    answer = () => ok(overviewOf());
    const { unmount } = render(<QbittorrentPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText("Trage qBittorrent unter Einstellungen → Erweiterungen → qBittorrent ein.")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Jetzt einrichten|Einstellungen/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/settings/extensions/qbittorrent", "/settings/extensions/qbittorrent"]);
    unmount();

    permissions.delete("extensions.manage");
    render(<QbittorrentPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText(/Ein Administrator kann qBittorrent/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("eine nicht eingerichtete Verbindung sagt, was fehlt, und führt zu den Einstellungen", async () => {
    const message = "Für qb fehlt das Passwort, und qBittorrent verlangt eine Anmeldung. Trage es in den Einstellungen ein.";
    answer = () => ok(overviewOf(failed({ state: "not_configured", message })));
    render(<QbittorrentPage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByText(message)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/qbittorrent");
    expect(screen.getByText("nicht eingerichtet")).toBeInTheDocument();
    expect(screen.queryByText(/^Torrents/)).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Grund, Erneut versuchen fragt ohne Zwischenspeicher nach, keine Abschnitte", async () => {
    const message = "qBittorrent „qb“ antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob qBittorrent läuft.";
    answer = () => ok(overviewOf(failed({ state: "unreachable", message })));
    render(<QbittorrentPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.queryByTestId("transfer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("no-active")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(problem.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("active")).toBeInTheDocument());
    expect(urls).toEqual(["/api/v1/ext/qbittorrent/overview", "/api/v1/ext/qbittorrent/overview?refresh=true"]);
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
  });

  it("abgelehnte Anmeldung: Meldung und Einstellungen, aber kein Knopf zum erneuten Versuchen", async () => {
    const message = "qBittorrent „qb“: Benutzername oder Passwort wurden abgelehnt. Prüfe beides in den Einstellungen …";
    answer = () => ok(overviewOf(failed({ state: "auth_failed", message })));
    render(<QbittorrentPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(problem.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
    expect(problem.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/qbittorrent");
    expect(screen.getByText("Anmeldung fehlgeschlagen", { selector: "span" })).toBeInTheDocument();
  });

  it("bleibt bei einem Fehler die Version stehen, steht der Grund dabei", async () => {
    answer = () => ok(overviewOf(failed({ state: "error", message: "qBittorrent „qb“ meldet einen Fehler (HTTP 500).", version: "v5.1.4" })));
    render(<QbittorrentPage />);
    expect(await screen.findByTestId("facts")).toHaveTextContent("Version v5.1.4");
    expect(screen.getByTestId("problem")).toHaveTextContent("(HTTP 500)");
    expect(screen.queryByTestId("counts")).not.toBeInTheDocument();
  });

  it("ein Ladefehler ist keine leere Seite: Grund und Erneut versuchen, danach die Daten", async () => {
    answer = () => new Response(JSON.stringify({ detail: "Datenbank gesperrt." }), { status: 500 });
    render(<QbittorrentPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Zustand von qBittorrent ließ sich gerade nicht laden: Datenbank gesperrt.");
    expect(screen.queryByText("Noch keine Verbindung")).not.toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("connection-qb")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });

  it("schlägt Aktualisieren fehl, bleiben die alten Daten stehen und der Fehler steht dabei", async () => {
    render(<QbittorrentPage />);
    await screen.findByTestId("connection-qb");
    answer = () => new Response(JSON.stringify({ detail: "Server überlastet." }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Server überlastet.");
    expect(screen.getByTestId("connection-qb")).toBeInTheDocument();
    expect(urls[1]).toBe("/api/v1/ext/qbittorrent/overview?refresh=true");
  });

  it("zeigt „Lade …“, solange die Antwort fehlt", async () => {
    let release: (r: Response) => void = () => {};
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    render(<QbittorrentPage />);
    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeDisabled();
    release(ok(overviewOf(connection())));
    expect(await screen.findByTestId("connection-qb")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("mehrere Verbindungen: je eine Karte; Zeilen brechen um statt über den Bildschirm hinauszuragen", async () => {
    answer = () => ok(overviewOf(connection({ name: "eins" }), connection({ name: "zwei", url: "http://zwei.example.com:8080" })));
    render(<QbittorrentPage />);
    expect(await screen.findByTestId("connection-eins")).toBeInTheDocument();
    expect(screen.getByTestId("connection-zwei")).toBeInTheDocument();
    const row = within(screen.getByTestId("connection-eins")).getAllByTestId("torrent")[0];
    expect(row.innerHTML).toContain("break-words");
    expect(row.innerHTML).not.toMatch(/\bw-\[|min-w-\[|whitespace-nowrap\s+w-/);
    expect(row.querySelector(".flex-wrap")).not.toBeNull();
  });

  it("ohne extensions.manage gibt es keinen Link zu den Einstellungen", async () => {
    permissions.delete("extensions.manage");
    answer = () => ok(overviewOf(failed({ state: "error", message: "Unter dieser Adresse läuft kein qBittorrent." })));
    render(<QbittorrentPage />);
    expect(await screen.findByTestId("problem")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Einstellungen" })).not.toBeInTheDocument();
  });
});

describe("Hilfen", () => {
  it.each([
    [0, "0 B"], [512, "512 B"], [1024, "1,0 KB"], [1536, "1,5 KB"], [10 * 1024, "10 KB"], [1280, "1,3 KB"], [12800, "13 KB"],
    [3 * MIB + 200 * 1024, "3,2 MB"], [500 * 1024 ** 3, "500 GB"], [5 * 1024 ** 4, "5,0 TB"],
  ])("sizeText %s -> %s", (count, expected) => {
    expect(sizeText(count)).toBe(expected);
  });

  it("speedText", () => {
    expect(speedText(2 * MIB)).toBe("2,0 MB/s");
  });

  it.each([
    [0, "< 1 Min."], [59, "< 1 Min."], [60, "1 Min."], [3599, "59 Min."], [3600, "1 Std."], [4320, "1 Std. 12 Min."],
    [86400, "1 Tag"], [90000, "1 Tag 1 Std."], [3 * 86400 + 3600, "3 Tage 1 Std."],
  ])("durationText %s -> %s", (seconds, expected) => {
    expect(durationText(seconds)).toBe(expected);
  });

  it("torrentDetail nennt die Restzeit nur beim Laden", () => {
    expect(torrentDetail(ubuntu)).toBe("42 % · ↓ 3,0 MB/s · ↑ 256 KB/s · noch 20 Min. · 6,0 GB");
    expect(torrentDetail({ ...ubuntu, state: "uploading" })).not.toContain("noch");
    expect(torrentDetail({ ...ubuntu, eta_seconds: null, size: null, upload_speed: 0 })).toBe("42 % · ↓ 3,0 MB/s");
  });
});
