import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UptimeKumaPage, type Connection, type Monitor, type Overview, type Summary } from "./UptimeKumaPage";

const monitor = (over: Partial<Monitor> & { name: string }): Monitor => ({
  type: "http", url: null, status: "up", response_ms: null, uptime_24h: null, cert_days_left: null, cert_valid: null, ...over,
});

const summaryOf = (monitors: Monitor[]): Summary => ({
  total: monitors.length,
  up: monitors.filter((m) => m.status === "up").length,
  down: monitors.filter((m) => m.status === "down").length,
  pending: monitors.filter((m) => m.status === "pending").length,
  maintenance: monitors.filter((m) => m.status === "maintenance").length,
});

const MONITORS: Monitor[] = [
  monitor({ name: "Router", type: "ping", url: "198.51.100.1", status: "down", uptime_24h: 94.12 }),
  monitor({ name: "NAS", type: "port", url: "198.51.100.20:5000", status: "pending", uptime_24h: 50 }),
  monitor({ name: "Nextcloud", url: "https://cloud.example.com/", response_ms: 1234, uptime_24h: 100, cert_days_left: 62, cert_valid: true }),
  monitor({ name: "Wiki", type: "keyword", url: "https://wiki.example.com/", response_ms: 45, uptime_24h: 99.93, cert_days_left: 9, cert_valid: true }),
];

const connection = (over: Partial<Connection> = {}): Connection => ({
  name: "kuma", url: "http://kuma.test:3001", state: "ok", message: null, monitors: MONITORS, summary: summaryOf(MONITORS),
  outages: [
    { monitor: "Wiki", time: "2026-10-12T08:01:00Z", message: "Keyword nicht gefunden" },
    { monitor: "Router", time: "2026-10-12T07:59:00Z", message: "" },
  ],
  status_page: true, ...over,
});

const overviewOf = (...connections: Connection[]): Overview => ({
  connections,
  summary: summaryOf(connections.flatMap((c) => c.monitors)),
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
    if (url.includes("/ext/uptime-kuma/overview")) return answer();
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("Uptime-Kuma-Seite", () => {
  it("zeigt Zähler, Monitore in der gelieferten Reihenfolge und die letzten Ausfälle", async () => {
    render(<UptimeKumaPage />);
    const card = within(await screen.findByTestId("connection-kuma"));

    expect(screen.getByText("1 ausgefallen")).toBeInTheDocument();
    const counters = card.getByText("Laufen").closest("div") as HTMLElement;
    expect(counters).toHaveTextContent("2");
    expect(card.getByText("Ausgefallen").closest("div")).toHaveTextContent("1");
    expect(card.getByText("Ausstehend").closest("div")).toHaveTextContent("1");
    expect(card.getByText("In Wartung").closest("div")).toHaveTextContent("0");

    const rows = screen.getAllByTestId("monitor");
    expect(rows.map((r) => within(r).getAllByText(/./)[0].textContent)).toEqual(["Router", "NAS", "Nextcloud", "Wiki"]);
    const router = within(rows[0]);
    expect(router.getByText("ausgefallen")).toBeInTheDocument();
    expect(router.getByText("ping · 198.51.100.1")).toBeInTheDocument();
    expect(router.getByText("94,12 % (24 Std.)")).toBeInTheDocument();
    expect(rows[0].className).toContain("bg-red");
    expect(rows[1].className).toContain("bg-amber");
    expect(rows[2].className).not.toContain("bg-");
    const cloud = within(rows[2]);
    expect(cloud.getByText("1.234 ms")).toBeInTheDocument();
    expect(cloud.getByText("100,00 % (24 Std.)")).toBeInTheDocument();
    expect(cloud.getByText("läuft")).toBeInTheDocument();
    expect(cloud.getByText("Zertifikat noch 62 Tage")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Zertifikat läuft in 9 Tagen ab")).toBeInTheDocument();

    const outages = within(screen.getByTestId("outages"));
    expect(outages.getByText("12.10.2026, 10:01 · Keyword nicht gefunden")).toBeInTheDocument();
    expect(outages.getByText("12.10.2026, 09:59")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Uptime Kuma öffnen" })).toHaveAttribute("href", "http://kuma.test:3001");
    expect(urls).toEqual(["/api/v1/ext/uptime-kuma/overview"]);
  });

  it("wenn alles läuft: grünes Abzeichen und keine Warnfarbe", async () => {
    const calm = [monitor({ name: "A" }), monitor({ name: "B" })];
    answer = () => ok(overviewOf(connection({ monitors: calm, summary: summaryOf(calm), outages: [] })));
    render(<UptimeKumaPage />);
    expect(await screen.findByText("alles in Ordnung")).toBeInTheDocument();
    expect(screen.getByText("In den letzten Messungen gab es keinen Ausfall.")).toBeInTheDocument();
    for (const row of screen.getAllByTestId("monitor")) expect(row.className).not.toContain("bg-");
  });

  it.each([
    [-3, null, "Zertifikat abgelaufen"],
    [0, true, "Zertifikat läuft heute ab"],
    [1, true, "Zertifikat läuft in 1 Tag ab"],
    [14, true, "Zertifikat läuft in 14 Tagen ab"],
    [15, true, "Zertifikat noch 15 Tage"],
    [40, false, "Zertifikat ungültig"],
    [null, false, "Zertifikat ungültig"],
  ])("Zertifikat mit %s Tagen (gültig: %s) heißt %s", async (days, valid, text) => {
    const list = [monitor({ name: "Seite", cert_days_left: days as number | null, cert_valid: valid as boolean | null })];
    answer = () => ok(overviewOf(connection({ monitors: list, summary: summaryOf(list), outages: [] })));
    render(<UptimeKumaPage />);
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it("ohne Status-Seite steht ein Hinweis statt der Ausfälle", async () => {
    answer = () => ok(overviewOf(connection({ status_page: false, outages: [], monitors: [monitor({ name: "A" })], summary: summaryOf([monitor({ name: "A" })]) })));
    render(<UptimeKumaPage />);
    expect(await screen.findByTestId("status-page-hint")).toHaveTextContent("Kurznamen einer Status-Seite");
    expect(screen.queryByText("Letzte Ausfälle")).not.toBeInTheDocument();
    expect(screen.queryByTestId("outages")).not.toBeInTheDocument();
  });

  it("ohne Eintrag: Hinweis mit Link zu den Einstellungen, ohne Recht der Verweis auf einen Administrator", async () => {
    answer = () => ok(overviewOf());
    const { unmount } = render(<UptimeKumaPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText("Trage Uptime Kuma unter Einstellungen → Erweiterungen → Uptime Kuma ein.")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Jetzt einrichten|Einstellungen/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/settings/extensions/uptime-kuma", "/settings/extensions/uptime-kuma"]);
    unmount();

    permissions.delete("extensions.manage");
    render(<UptimeKumaPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText(/Ein Administrator kann Uptime Kuma/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("eine nicht eingerichtete Verbindung sagt, was fehlt, und führt zu den Einstellungen", async () => {
    const message = "Für kuma fehlt der API-Schlüssel oder eine Status-Seite.";
    answer = () => ok(overviewOf(connection({ state: "not_configured", message, monitors: [], summary: null, outages: [], status_page: false })));
    render(<UptimeKumaPage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByText(message)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/uptime-kuma");
    expect(screen.getByText("nicht eingerichtet")).toBeInTheDocument();
    expect(screen.queryByText("Laufen")).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Grund, Erneut versuchen fragt ohne Zwischenspeicher nach, keine Zähler", async () => {
    const message = "Uptime Kuma „kuma“ antwortet nicht (Zeitüberschreitung).";
    answer = () => ok(overviewOf(connection({ state: "unreachable", message, monitors: [], summary: null, outages: [] })));
    render(<UptimeKumaPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.queryByText("Laufen")).not.toBeInTheDocument();
    expect(screen.queryByText("In den letzten Messungen gab es keinen Ausfall.")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(problem.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("connection-kuma")).toBeInTheDocument());
    expect(urls).toEqual(["/api/v1/ext/uptime-kuma/overview", "/api/v1/ext/uptime-kuma/overview?refresh=true"]);
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
  });

  it("abgelehnter Schlüssel: Meldung, und was die Status-Seite verrät, bleibt sichtbar", async () => {
    const message = "Uptime Kuma „kuma“ hat den API-Schlüssel nicht akzeptiert.";
    answer = () => ok(overviewOf(connection({ state: "auth_failed", message })));
    render(<UptimeKumaPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(problem.getByText("Angezeigt wird, was trotzdem zu lesen war.")).toBeInTheDocument();
    expect(problem.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/uptime-kuma");
    expect(screen.getByText("1 ausgefallen")).toBeInTheDocument();
    expect(screen.getAllByTestId("monitor")).toHaveLength(4);
    expect(screen.queryByText("In den letzten Messungen gab es keinen Ausfall.")).not.toBeInTheDocument();
  });

  it("ein Ladefehler ist keine leere Seite: Grund und Erneut versuchen, danach die Daten", async () => {
    answer = () => new Response(JSON.stringify({ detail: "Datenbank gesperrt." }), { status: 500 });
    render(<UptimeKumaPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Zustand von Uptime Kuma ließ sich gerade nicht laden: Datenbank gesperrt.");
    expect(screen.queryByText("Noch keine Verbindung")).not.toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("connection-kuma")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });

  it("schlägt Aktualisieren fehl, bleiben die alten Daten stehen und der Fehler steht dabei", async () => {
    render(<UptimeKumaPage />);
    await screen.findByTestId("connection-kuma");
    answer = () => new Response(JSON.stringify({ detail: "Server überlastet." }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Server überlastet.");
    expect(screen.getByTestId("connection-kuma")).toBeInTheDocument();
    expect(urls[1]).toBe("/api/v1/ext/uptime-kuma/overview?refresh=true");
  });

  it("zeigt „Lade …“, solange die Antwort fehlt", async () => {
    let release: (r: Response) => void = () => {};
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    render(<UptimeKumaPage />);
    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeDisabled();
    release(ok(overviewOf(connection())));
    expect(await screen.findByTestId("connection-kuma")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("bei vielen Monitoren gibt es eine Suche", async () => {
    const many = Array.from({ length: 9 }, (_, i) => monitor({ name: `Dienst ${i + 1}`, url: `https://d${i + 1}.example.com/` }));
    answer = () => ok(overviewOf(connection({ monitors: many, summary: summaryOf(many), outages: [] })));
    render(<UptimeKumaPage />);
    const search = await screen.findByRole("textbox", { name: "Monitore durchsuchen" });
    expect(screen.getAllByTestId("monitor")).toHaveLength(9);
    fireEvent.change(search, { target: { value: "dienst 7" } });
    expect(screen.getAllByTestId("monitor")).toHaveLength(1);
    fireEvent.change(search, { target: { value: "gibt es nicht" } });
    expect(screen.getByText("Kein Monitor passt zur Suche.")).toBeInTheDocument();
  });

  it("mehrere Verbindungen: je eine Karte; Zeilen brechen um statt über den Bildschirm hinauszuragen", async () => {
    answer = () => ok(overviewOf(connection({ name: "eins" }), connection({ name: "zwei", url: "http://zwei.test:3001" })));
    render(<UptimeKumaPage />);
    expect(await screen.findByTestId("connection-eins")).toBeInTheDocument();
    expect(screen.getByTestId("connection-zwei")).toBeInTheDocument();
    const row = within(screen.getByTestId("connection-eins")).getAllByTestId("monitor")[0];
    expect(row.className).toContain("flex-wrap");
    expect(row.innerHTML).toContain("break-words");
    expect(row.innerHTML).not.toMatch(/\bw-\[|min-w-\[|whitespace-nowrap\s+w-/);
  });

  it("ohne extensions.manage gibt es keinen Link zu den Einstellungen", async () => {
    permissions.delete("extensions.manage");
    answer = () => ok(overviewOf(connection({ state: "error", message: "Unter dieser Adresse antwortet kein Uptime Kuma oder /metrics ist abgeschaltet.", monitors: [], summary: null, outages: [] })));
    render(<UptimeKumaPage />);
    expect(await screen.findByTestId("problem")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Einstellungen" })).not.toBeInTheDocument();
  });
});
