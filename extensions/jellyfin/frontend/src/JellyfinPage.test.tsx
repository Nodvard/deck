import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  JellyfinPage, episodeCode, mediaDetail, numberText,
  type Connection, type LatestItem, type LibraryCounts, type Overview, type Playback,
} from "./JellyfinPage";

const media = { series: null, season: null, episode: null, artist: null, year: null };

const film: Playback = {
  ...media, title: "Der Beispielfilm", kind: "movie", year: 2019, user: "anna", device: "Firefox", client: "Jellyfin Web",
  play_method: "direct_play", transcoding: false, progress_percent: 25, paused: false,
};
const folge: Playback = {
  ...media, title: "Der Besuch", kind: "episode", series: "Die Beispielserie", season: 2, episode: 5, user: "ben",
  device: "Wohnzimmer-TV", client: "Jellyfin Android TV", play_method: "transcode", transcoding: true, progress_percent: 33, paused: true,
};

const COUNTS: LibraryCounts = {
  movies: 482, series: 37, episodes: 1296, albums: 318, songs: 4210, music_videos: 0, box_sets: 12, books: 0,
};

const LATEST: LatestItem[] = [
  { ...media, title: "Der zweite Beispielfilm", kind: "movie", year: 2024, added_at: "2026-10-11T18:22:31Z" },
  { ...media, title: "Der Besuch", kind: "episode", series: "Die Beispielserie", season: 2, episode: 5, added_at: "2026-10-10T07:05:00Z" },
];

const connection = (over: Partial<Connection> = {}): Connection => ({
  name: "jf",
  url: "http://jellyfin.example.com:8096",
  state: "ok",
  message: null,
  server_name: "Heimkino",
  version: "10.10.7",
  playback_count: 2,
  playbacks: [film, folge],
  counts: COUNTS,
  latest: LATEST,
  problems: [],
  ...over,
});

const failed = (over: Partial<Connection>): Connection =>
  connection({ playback_count: null, playbacks: null, counts: null, latest: null, server_name: null, version: null, ...over });

const overviewOf = (...connections: Connection[]): Overview => ({
  connections,
  summary: {
    connections: connections.length,
    playing: connections.reduce((n, c) => n + (c.playback_count ?? 0), 0),
    transcoding: connections.reduce((n, c) => n + (c.playbacks ?? []).filter((p) => p.transcoding).length, 0),
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
    timezone: "Europe/Berlin",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    urls.push(url);
    if (url.includes("/ext/jellyfin/overview")) return answer();
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("Jellyfin-Seite", () => {
  it("zeigt Server, laufende Wiedergaben, Bibliotheken und neue Medien", async () => {
    render(<JellyfinPage />);
    const card = within(await screen.findByTestId("connection-jf"));

    expect(screen.getByText("2 laufen", { selector: "span" })).toBeInTheDocument();
    expect(card.getByTestId("facts")).toHaveTextContent("Server Heimkino · Version 10.10.7");
    expect(screen.getByRole("link", { name: "Jellyfin öffnen" })).toHaveAttribute("href", "http://jellyfin.example.com:8096");

    const rows = card.getAllByTestId("playback");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Der Beispielfilm")).toBeInTheDocument();
    expect(within(rows[0]).getByText("2019")).toBeInTheDocument();
    expect(within(rows[0]).getByText("anna · Firefox")).toBeInTheDocument();
    expect(within(rows[0]).getByText("direkt")).toBeInTheDocument();
    expect(within(rows[0]).getByText("25 %")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(within(rows[0]).queryByText("pausiert")).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("Der Besuch")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Die Beispielserie S02E05")).toBeInTheDocument();
    expect(within(rows[1]).getByText("ben · Wohnzimmer-TV")).toBeInTheDocument();
    expect(within(rows[1]).getByText("transkodiert")).toBeInTheDocument();
    expect(within(rows[1]).getByText("pausiert")).toBeInTheDocument();
    expect(card.getByText("Läuft gerade (2)")).toBeInTheDocument();

    const counts = within(card.getByTestId("counts"));
    expect(counts.getByText("Filme").closest("div")).toHaveTextContent("482");
    expect(counts.getByText("Folgen").closest("div")).toHaveTextContent("1.296");
    expect(counts.getByText("Titel").closest("div")).toHaveTextContent("4.210");
    expect(counts.queryByText("Bücher")).not.toBeInTheDocument();
    expect(counts.queryByText("Musikvideos")).not.toBeInTheDocument();

    const latest = within(card.getByTestId("latest")).getAllByTestId("latest-item");
    expect(latest).toHaveLength(2);
    expect(within(latest[0]).getByText("Der zweite Beispielfilm")).toBeInTheDocument();
    expect(within(latest[0]).getByText("2024")).toBeInTheDocument();
    expect(within(latest[0]).getByText("11.10.2026")).toBeInTheDocument();
    expect(within(latest[1]).getByText("Die Beispielserie S02E05")).toBeInTheDocument();
    expect(urls).toEqual(["/api/v1/ext/jellyfin/overview"]);
  });

  it("ohne Wiedergabe steht das da, und die Bibliotheken bleiben sichtbar", async () => {
    answer = () => ok(overviewOf(connection({ playback_count: 0, playbacks: [], latest: [] })));
    render(<JellyfinPage />);
    expect(await screen.findByText("nichts läuft", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByTestId("no-playbacks")).toHaveTextContent("Gerade läuft nichts.");
    expect(screen.queryByTestId("playbacks")).not.toBeInTheDocument();
    expect(screen.getByTestId("no-latest")).toHaveTextContent("Noch nichts hinzugefügt.");
    expect(screen.getByTestId("counts")).toBeInTheDocument();
  });

  it("leere Bibliotheken sagen es ausdrücklich", async () => {
    answer = () => ok(overviewOf(connection({ counts: { ...COUNTS, movies: 0, series: 0, episodes: 0, albums: 0, songs: 0, box_sets: 0 } })));
    render(<JellyfinPage />);
    expect(await screen.findByTestId("no-media")).toHaveTextContent("Noch keine Medien in den Bibliotheken.");
    expect(screen.queryByTestId("counts")).not.toBeInTheDocument();
  });

  it("eine lange Liste sagt, wie viele nicht gezeigt werden", async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ ...film, title: `Film ${i}`, user: `u${i}` }));
    answer = () => ok(overviewOf(connection({ playback_count: 120, playbacks: many })));
    render(<JellyfinPage />);
    expect(await screen.findByText("Läuft gerade (120)")).toBeInTheDocument();
    expect(screen.getAllByTestId("playback")).toHaveLength(50);
    expect(screen.getByText("… und 70 weitere (hier werden die ersten 50 gezeigt).")).toBeInTheDocument();
    expect(screen.getByText("120 laufen", { selector: "span" })).toBeInTheDocument();
  });

  it("Titel und Namen aus Jellyfin erscheinen nur als Text, nie als HTML", async () => {
    const evil = { ...film, title: "<img src=x onerror=alert(1)>", user: "<b>anna</b>" };
    answer = () => ok(overviewOf(connection({ playback_count: 1, playbacks: [evil], latest: [] })));
    const { container } = render(<JellyfinPage />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("Musik zeigt den Künstler, eine Wiedergabe ohne Länge keinen Fortschrittsbalken", async () => {
    const lied: Playback = { ...film, title: "Lied", kind: "audio", artist: "Band", year: null, progress_percent: null, play_method: null };
    answer = () => ok(overviewOf(connection({ playback_count: 1, playbacks: [lied] })));
    render(<JellyfinPage />);
    const row = within(await screen.findByTestId("playback"));
    expect(row.getByText("Band")).toBeInTheDocument();
    expect(row.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(row.queryByText(/%/)).not.toBeInTheDocument();
    expect(row.queryByText("direkt")).not.toBeInTheDocument();
  });

  it("scheitert nur ein Abschnitt, bleiben die anderen stehen und der Grund steht dabei", async () => {
    const problem = "Laufende Wiedergaben: Jellyfin „jf“ meldet einen Fehler (HTTP 500). Schau in das Protokoll von Jellyfin.";
    answer = () => ok(overviewOf(connection({ playback_count: null, playbacks: null, problems: [problem] })));
    render(<JellyfinPage />);
    expect(await screen.findByTestId("problems")).toHaveTextContent(problem);
    expect(screen.getByText("unvollständig", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByTestId("no-playbacks")).not.toBeInTheDocument();
    expect(screen.getByTestId("counts")).toBeInTheDocument();
    expect(screen.getByTestId("latest")).toBeInTheDocument();
  });

  it("ohne Eintrag: Hinweis mit Link zu den Einstellungen, ohne Recht der Verweis auf einen Administrator", async () => {
    answer = () => ok(overviewOf());
    const { unmount } = render(<JellyfinPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText("Trage Jellyfin unter Einstellungen → Erweiterungen → Jellyfin ein.")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Jetzt einrichten|Einstellungen/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/settings/extensions/jellyfin", "/settings/extensions/jellyfin"]);
    unmount();

    permissions.delete("extensions.manage");
    render(<JellyfinPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText(/Ein Administrator kann Jellyfin/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("eine nicht eingerichtete Verbindung sagt, was fehlt, und führt zu den Einstellungen", async () => {
    const message = "Für jf fehlt der API-Schlüssel. Unter Einstellungen → Erweiterungen → Jellyfin eintragen.";
    answer = () => ok(overviewOf(failed({ state: "not_configured", message })));
    render(<JellyfinPage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByText(message)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/jellyfin");
    expect(screen.getByText("nicht eingerichtet")).toBeInTheDocument();
    expect(screen.queryByText("Bibliotheken")).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Grund, Erneut versuchen fragt ohne Zwischenspeicher nach, keine Abschnitte", async () => {
    const message = "Jellyfin „jf“ antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob Jellyfin läuft.";
    answer = () => ok(overviewOf(failed({ state: "unreachable", message })));
    render(<JellyfinPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.queryByText("Bibliotheken")).not.toBeInTheDocument();
    expect(screen.queryByTestId("no-playbacks")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(problem.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("playbacks")).toBeInTheDocument());
    expect(urls).toEqual(["/api/v1/ext/jellyfin/overview", "/api/v1/ext/jellyfin/overview?refresh=true"]);
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
  });

  it("abgelehnter Schlüssel: Meldung und Einstellungen, aber kein Knopf zum erneuten Versuchen", async () => {
    const message = "Jellyfin „jf“: Der API-Schlüssel wurde abgelehnt (HTTP 401). Prüfe in Jellyfin unter Dashboard → API-Schlüssel …";
    answer = () => ok(overviewOf(failed({ state: "auth_failed", message })));
    render(<JellyfinPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(problem.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
    expect(problem.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/jellyfin");
    expect(screen.getByText("Anmeldung fehlgeschlagen", { selector: "span" })).toBeInTheDocument();
  });

  it("bleibt bei einem Fehler die Version stehen, steht der Grund dabei", async () => {
    answer = () => ok(overviewOf(failed({
      state: "error", message: "Jellyfin „jf“ meldet einen Fehler (HTTP 500).", server_name: "Heimkino", version: "10.10.7",
    })));
    render(<JellyfinPage />);
    expect(await screen.findByTestId("facts")).toHaveTextContent("Server Heimkino · Version 10.10.7");
    expect(screen.getByTestId("problem")).toHaveTextContent("(HTTP 500)");
    expect(screen.queryByTestId("counts")).not.toBeInTheDocument();
  });

  it("ein Ladefehler ist keine leere Seite: Grund und Erneut versuchen, danach die Daten", async () => {
    answer = () => new Response(JSON.stringify({ detail: "Datenbank gesperrt." }), { status: 500 });
    render(<JellyfinPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Zustand von Jellyfin ließ sich gerade nicht laden: Datenbank gesperrt.");
    expect(screen.queryByText("Noch keine Verbindung")).not.toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("connection-jf")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });

  it("schlägt Aktualisieren fehl, bleiben die alten Daten stehen und der Fehler steht dabei", async () => {
    render(<JellyfinPage />);
    await screen.findByTestId("connection-jf");
    answer = () => new Response(JSON.stringify({ detail: "Server überlastet." }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Server überlastet.");
    expect(screen.getByTestId("connection-jf")).toBeInTheDocument();
    expect(urls[1]).toBe("/api/v1/ext/jellyfin/overview?refresh=true");
  });

  it("zeigt „Lade …“, solange die Antwort fehlt", async () => {
    let release: (r: Response) => void = () => {};
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    render(<JellyfinPage />);
    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeDisabled();
    release(ok(overviewOf(connection())));
    expect(await screen.findByTestId("connection-jf")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("mehrere Verbindungen: je eine Karte; Zeilen brechen um statt über den Bildschirm hinauszuragen", async () => {
    answer = () => ok(overviewOf(connection({ name: "eins" }), connection({ name: "zwei", url: "http://zwei.example.com:8096" })));
    render(<JellyfinPage />);
    expect(await screen.findByTestId("connection-eins")).toBeInTheDocument();
    expect(screen.getByTestId("connection-zwei")).toBeInTheDocument();
    for (const testId of ["playback", "latest-item"]) {
      const row = within(screen.getByTestId("connection-eins")).getAllByTestId(testId)[0];
      expect(row.innerHTML).toContain("break-words");
      expect(row.innerHTML).not.toMatch(/\bw-\[|min-w-\[|whitespace-nowrap\s+w-/);
    }
    expect(within(screen.getByTestId("connection-eins")).getAllByTestId("playback")[0].querySelector(".flex-wrap")).not.toBeNull();
  });

  it("verlinkt nur eine Webadresse, kein `javascript:` aus der Antwort", async () => {
    answer = () => ok(overviewOf(connection({ name: "gut" }), connection({ name: "schlecht", url: "javascript:alert(1)" })));
    render(<JellyfinPage />);
    expect(await screen.findByTestId("connection-schlecht")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Jellyfin öffnen" });
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["http://jellyfin.example.com:8096"]);
  });

  it("ohne extensions.manage gibt es keinen Link zu den Einstellungen", async () => {
    permissions.delete("extensions.manage");
    answer = () => ok(overviewOf(failed({ state: "error", message: "Unter dieser Adresse läuft kein Jellyfin." })));
    render(<JellyfinPage />);
    expect(await screen.findByTestId("problem")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Einstellungen" })).not.toBeInTheDocument();
  });
});

describe("Hilfen", () => {
  it.each([
    [null, null, ""],
    [2, 5, "S02E05"],
    [12, 104, "S12E104"],
    [3, null, "S03"],
    [null, 9, "E09"],
  ])("episodeCode %s %s -> %s", (season, episode, expected) => {
    expect(episodeCode(season, episode)).toBe(expected);
  });

  it.each([[0, "0"], [999, "999"], [1296, "1.296"], [1234567, "1.234.567"]])("numberText %s -> %s", (count, expected) => {
    expect(numberText(count)).toBe(expected);
  });

  it("mediaDetail", () => {
    expect(mediaDetail(folge)).toBe("Die Beispielserie S02E05");
    expect(mediaDetail(film)).toBe("2019");
    expect(mediaDetail({ ...film, kind: "audio", artist: "Band" })).toBe("Band");
    expect(mediaDetail({ ...film, kind: "audio", artist: null })).toBe("");
    expect(mediaDetail({ ...film, year: null })).toBe("");
  });
});
