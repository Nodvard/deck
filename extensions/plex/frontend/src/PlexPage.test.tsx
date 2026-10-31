import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PlexPage, episodeCode, libraryHint, mediaDetail, numberText,
  type Connection, type LatestItem, type Library, type Overview, type Playback,
} from "./PlexPage";

const media = { series: null, season: null, episode: null, artist: null, year: null };

const film: Playback = {
  ...media, title: "Der Beispielfilm", kind: "movie", year: 2019, user: "anna", device: "Firefox", client: "Plex Web",
  play_method: "direct_play", transcoding: false, progress_percent: 25, paused: false,
};
const folge: Playback = {
  ...media, title: "Der Besuch", kind: "episode", series: "Die Beispielserie", season: 2, episode: 5, user: "ben",
  device: "Wohnzimmer-TV", client: "Plex for Android (TV)", play_method: "transcode", transcoding: true, progress_percent: 33, paused: true,
};

const LIBRARIES: Library[] = [
  { title: "Kino", kind: "movie", count: 482, scanning: false },
  { title: "Fernsehen", kind: "show", count: 1296, scanning: false },
  { title: "Musik", kind: "artist", count: 164, scanning: true },
];

const LATEST: LatestItem[] = [
  { ...media, title: "Der zweite Beispielfilm", kind: "movie", year: 2024, added_at: "2026-10-11T18:22:31Z" },
  { ...media, title: "Der Besuch", kind: "episode", series: "Die Beispielserie", season: 2, episode: 5, added_at: "2026-10-10T07:05:00Z" },
];

const connection = (over: Partial<Connection> = {}): Connection => ({
  name: "jf",
  url: "http://plex.example.com:32400",
  state: "ok",
  message: null,
  server_name: "Heimkino",
  version: "1.41.3.9314-a0bfb8370",
  playback_count: 2,
  playbacks: [film, folge],
  libraries: LIBRARIES,
  latest: LATEST,
  problems: [],
  ...over,
});

const failed = (over: Partial<Connection>): Connection =>
  connection({ playback_count: null, playbacks: null, libraries: null, latest: null, server_name: null, version: null, ...over });

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
    if (url.includes("/ext/plex/overview")) return answer();
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("Plex-Seite", () => {
  it("zeigt Server, laufende Wiedergaben, Bibliotheken und neue Medien", async () => {
    render(<PlexPage />);
    const card = within(await screen.findByTestId("connection-jf"));

    expect(screen.getByText("2 laufen", { selector: "span" })).toBeInTheDocument();
    expect(card.getByTestId("facts")).toHaveTextContent("Server Heimkino · Version 1.41.3.9314-a0bfb8370");
    expect(screen.getByRole("link", { name: "Plex öffnen" })).toHaveAttribute("href", "http://plex.example.com:32400");

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

    const libraries = within(card.getByTestId("libraries"));
    const kino = libraries.getByText("Kino").closest("div.panel");
    expect(kino).toHaveTextContent("482");
    expect(kino).toHaveTextContent("Filme");
    expect(kino).not.toHaveTextContent("wird aktualisiert");
    expect(libraries.getByText("Fernsehen").closest("div.panel")).toHaveTextContent("1.296");
    expect(libraries.getByText("Fernsehen").closest("div.panel")).toHaveTextContent("Serien");
    const musik = libraries.getByText("Musik").closest("div.panel");
    expect(musik).toHaveTextContent("164");
    expect(musik).toHaveTextContent("Künstler · wird aktualisiert");

    const latest = within(card.getByTestId("latest")).getAllByTestId("latest-item");
    expect(latest).toHaveLength(2);
    expect(within(latest[0]).getByText("Der zweite Beispielfilm")).toBeInTheDocument();
    expect(within(latest[0]).getByText("2024")).toBeInTheDocument();
    expect(within(latest[0]).getByText("11.10.2026")).toBeInTheDocument();
    expect(within(latest[1]).getByText("Die Beispielserie S02E05")).toBeInTheDocument();
    expect(urls).toEqual(["/api/v1/ext/plex/overview"]);
  });

  it("ohne Wiedergabe steht das da, und die Bibliotheken bleiben sichtbar", async () => {
    answer = () => ok(overviewOf(connection({ playback_count: 0, playbacks: [], latest: [] })));
    render(<PlexPage />);
    expect(await screen.findByText("nichts läuft", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByTestId("no-playbacks")).toHaveTextContent("Gerade läuft nichts.");
    expect(screen.queryByTestId("playbacks")).not.toBeInTheDocument();
    expect(screen.getByTestId("no-latest")).toHaveTextContent("Noch nichts hinzugefügt.");
    expect(screen.getByTestId("libraries")).toBeInTheDocument();
  });

  it("ohne Bibliotheken sagt die Seite es ausdrücklich", async () => {
    answer = () => ok(overviewOf(connection({ libraries: [] })));
    render(<PlexPage />);
    expect(await screen.findByTestId("no-libraries")).toHaveTextContent("Noch keine Bibliotheken eingerichtet.");
    expect(screen.queryByTestId("libraries")).not.toBeInTheDocument();
  });

  it("eine Bibliothek ohne lesbare Zahl zeigt einen Strich statt einer Null", async () => {
    answer = () => ok(overviewOf(connection({ libraries: [{ title: "Kino", kind: "movie", count: null, scanning: false }, { title: "Leer", kind: "photo", count: 0, scanning: false }] })));
    render(<PlexPage />);
    const libraries = within(await screen.findByTestId("libraries"));
    expect(libraries.getByText("Kino").closest("div.panel")).toHaveTextContent("–");
    expect(libraries.getByText("Leer").closest("div.panel")).toHaveTextContent("0");
    expect(libraries.getByText("Leer").closest("div.panel")).toHaveTextContent("Einträge");
  });

  it("eine lange Liste sagt, wie viele nicht gezeigt werden", async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ ...film, title: `Film ${i}`, user: `u${i}` }));
    answer = () => ok(overviewOf(connection({ playback_count: 120, playbacks: many })));
    render(<PlexPage />);
    expect(await screen.findByText("Läuft gerade (120)")).toBeInTheDocument();
    expect(screen.getAllByTestId("playback")).toHaveLength(50);
    expect(screen.getByText("… und 70 weitere (hier werden die ersten 50 gezeigt).")).toBeInTheDocument();
    expect(screen.getByText("120 laufen", { selector: "span" })).toBeInTheDocument();
  });

  it("Titel und Namen aus Plex erscheinen nur als Text, nie als HTML", async () => {
    const evil = { ...film, title: "<img src=x onerror=alert(1)>", user: "<b>anna</b>" };
    answer = () => ok(overviewOf(connection({ playback_count: 1, playbacks: [evil], latest: [] })));
    const { container } = render(<PlexPage />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });

  it("Musik zeigt den Künstler, eine Wiedergabe ohne Länge keinen Fortschrittsbalken", async () => {
    const lied: Playback = { ...film, title: "Lied", kind: "audio", artist: "Band", year: null, progress_percent: null, play_method: null };
    answer = () => ok(overviewOf(connection({ playback_count: 1, playbacks: [lied] })));
    render(<PlexPage />);
    const row = within(await screen.findByTestId("playback"));
    expect(row.getByText("Band")).toBeInTheDocument();
    expect(row.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(row.queryByText(/%/)).not.toBeInTheDocument();
    expect(row.queryByText("direkt")).not.toBeInTheDocument();
  });

  it("scheitert nur ein Abschnitt, bleiben die anderen stehen und der Grund steht dabei", async () => {
    const problem = "Laufende Wiedergaben: Plex „jf“ meldet einen Fehler (HTTP 500). Schau in das Protokoll von Plex.";
    answer = () => ok(overviewOf(connection({ playback_count: null, playbacks: null, problems: [problem] })));
    render(<PlexPage />);
    expect(await screen.findByTestId("problems")).toHaveTextContent(problem);
    expect(screen.getByText("unvollständig", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByTestId("no-playbacks")).not.toBeInTheDocument();
    expect(screen.getByTestId("libraries")).toBeInTheDocument();
    expect(screen.getByTestId("latest")).toBeInTheDocument();
  });

  it("ohne Eintrag: Hinweis mit Link zu den Einstellungen, ohne Recht der Verweis auf einen Administrator", async () => {
    answer = () => ok(overviewOf());
    const { unmount } = render(<PlexPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText("Trage Plex unter Einstellungen → Erweiterungen → Plex ein.")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Jetzt einrichten|Einstellungen/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/settings/extensions/plex", "/settings/extensions/plex"]);
    unmount();

    permissions.delete("extensions.manage");
    render(<PlexPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText(/Ein Administrator kann Plex/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("eine nicht eingerichtete Verbindung sagt, was fehlt, und führt zu den Einstellungen", async () => {
    const message = "Für jf fehlt der Plex-Token. Unter Einstellungen → Erweiterungen → Plex eintragen.";
    answer = () => ok(overviewOf(failed({ state: "not_configured", message })));
    render(<PlexPage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByText(message)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/plex");
    expect(screen.getByText("nicht eingerichtet")).toBeInTheDocument();
    expect(screen.queryByText("Bibliotheken")).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Grund, Erneut versuchen fragt ohne Zwischenspeicher nach, keine Abschnitte", async () => {
    const message = "Plex „jf“ antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob Plex läuft.";
    answer = () => ok(overviewOf(failed({ state: "unreachable", message })));
    render(<PlexPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.queryByText("Bibliotheken")).not.toBeInTheDocument();
    expect(screen.queryByTestId("no-playbacks")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(problem.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("playbacks")).toBeInTheDocument());
    expect(urls).toEqual(["/api/v1/ext/plex/overview", "/api/v1/ext/plex/overview?refresh=true"]);
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
  });

  it("abgelehnter Token: Meldung und Einstellungen, aber kein Knopf zum erneuten Versuchen", async () => {
    const message = "Plex „jf“: Der Token wurde abgelehnt (HTTP 401). Trage in den Einstellungen einen gültigen Token ein …";
    answer = () => ok(overviewOf(failed({ state: "auth_failed", message })));
    render(<PlexPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(problem.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
    expect(problem.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/plex");
    expect(screen.getByText("Anmeldung fehlgeschlagen", { selector: "span" })).toBeInTheDocument();
  });

  it("bleibt bei einem Fehler die Version stehen, steht der Grund dabei", async () => {
    answer = () => ok(overviewOf(failed({
      state: "error", message: "Plex „jf“ meldet einen Fehler (HTTP 500).", server_name: "Heimkino", version: "1.41.3.9314-a0bfb8370",
    })));
    render(<PlexPage />);
    expect(await screen.findByTestId("facts")).toHaveTextContent("Server Heimkino · Version 1.41.3.9314-a0bfb8370");
    expect(screen.getByTestId("problem")).toHaveTextContent("(HTTP 500)");
    expect(screen.queryByTestId("libraries")).not.toBeInTheDocument();
  });

  it("ein Ladefehler ist keine leere Seite: Grund und Erneut versuchen, danach die Daten", async () => {
    answer = () => new Response(JSON.stringify({ detail: "Datenbank gesperrt." }), { status: 500 });
    render(<PlexPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Zustand von Plex ließ sich gerade nicht laden: Datenbank gesperrt.");
    expect(screen.queryByText("Noch keine Verbindung")).not.toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("connection-jf")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });

  it("schlägt Aktualisieren fehl, bleiben die alten Daten stehen und der Fehler steht dabei", async () => {
    render(<PlexPage />);
    await screen.findByTestId("connection-jf");
    answer = () => new Response(JSON.stringify({ detail: "Server überlastet." }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Server überlastet.");
    expect(screen.getByTestId("connection-jf")).toBeInTheDocument();
    expect(urls[1]).toBe("/api/v1/ext/plex/overview?refresh=true");
  });

  it("zeigt „Lade …“, solange die Antwort fehlt", async () => {
    let release: (r: Response) => void = () => {};
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    render(<PlexPage />);
    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeDisabled();
    release(ok(overviewOf(connection())));
    expect(await screen.findByTestId("connection-jf")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("mehrere Verbindungen: je eine Karte; Zeilen brechen um statt über den Bildschirm hinauszuragen", async () => {
    answer = () => ok(overviewOf(connection({ name: "eins" }), connection({ name: "zwei", url: "http://zwei.example.com:32400" })));
    render(<PlexPage />);
    expect(await screen.findByTestId("connection-eins")).toBeInTheDocument();
    expect(screen.getByTestId("connection-zwei")).toBeInTheDocument();
    for (const testId of ["playback", "latest-item"]) {
      const row = within(screen.getByTestId("connection-eins")).getAllByTestId(testId)[0];
      expect(row.innerHTML).toContain("break-words");
      expect(row.innerHTML).not.toMatch(/\bw-\[|min-w-\[|whitespace-nowrap\s+w-/);
    }
    expect(within(screen.getByTestId("connection-eins")).getAllByTestId("playback")[0].querySelector(".flex-wrap")).not.toBeNull();
  });

  it("ohne extensions.manage gibt es keinen Link zu den Einstellungen", async () => {
    permissions.delete("extensions.manage");
    answer = () => ok(overviewOf(failed({ state: "error", message: "Unter dieser Adresse läuft kein Plex." })));
    render(<PlexPage />);
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

  it("libraryHint", () => {
    const lib = { title: "x", kind: "movie", count: 1, scanning: false };
    expect(libraryHint(lib)).toBe("Filme");
    expect(libraryHint({ ...lib, kind: "show" })).toBe("Serien");
    expect(libraryHint({ ...lib, kind: "artist", scanning: true })).toBe("Künstler · wird aktualisiert");
    expect(libraryHint({ ...lib, kind: "other" })).toBe("Einträge");
  });

  it("mediaDetail", () => {
    expect(mediaDetail(folge)).toBe("Die Beispielserie S02E05");
    expect(mediaDetail(film)).toBe("2019");
    expect(mediaDetail({ ...film, kind: "audio", artist: "Band" })).toBe("Band");
    expect(mediaDetail({ ...film, kind: "audio", artist: null })).toBe("");
    expect(mediaDetail({ ...film, year: null })).toBe("");
    expect(mediaDetail({ ...folge, kind: "season", episode: null })).toBe("Die Beispielserie S02");
  });
});
