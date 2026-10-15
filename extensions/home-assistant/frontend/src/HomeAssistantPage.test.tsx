import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeAssistantPage, valueText, type Connection, type Counts, type EntityValue, type Overview } from "./HomeAssistantPage";

const COUNTS: Counts = { total: 20, updates: 2, unavailable: 4, unknown: 2 };

const entity = (over: Partial<EntityValue> & { entity_id: string }): EntityValue => ({
  name: over.entity_id, status: "ok", state: null, unit: null, ...over,
});

const connection = (over: Partial<Connection> = {}): Connection => ({
  name: "ha",
  url: "http://ha.example.com:8123",
  state: "ok",
  message: null,
  version: "2026.10.1",
  location_name: "Zuhause",
  counts: COUNTS,
  updates: [
    { entity_id: "update.home_assistant_core_update", name: "Home Assistant Core Update", installed_version: "2026.10.1", latest_version: "2026.10.2" },
    { entity_id: "update.mosquitto_broker_update", name: "Mosquitto broker Update", installed_version: "6.5.0", latest_version: "6.5.1" },
  ],
  unavailable: [
    { entity_id: "sensor.balkon_temperatur", name: "Balkon Temperatur" },
    { entity_id: "media_player.fernseher", name: "Fernseher" },
    { entity_id: "light.garten_lampe", name: "Garten Lampe" },
    { entity_id: "update.wled_firmware", name: "WLED Regal Firmware" },
  ],
  entities: [
    entity({ entity_id: "sensor.wohnzimmer_temperatur", name: "Wohnzimmer Temperatur", state: "21.5", unit: "°C" }),
    entity({ entity_id: "sensor.keller_luftfeuchtigkeit", name: "Keller Luftfeuchtigkeit", state: "58", unit: "%" }),
    entity({ entity_id: "sensor.balkon_temperatur", name: "Balkon Temperatur", status: "unavailable" }),
    entity({ entity_id: "sensor.nas_cpu_auslastung", name: "NAS CPU Auslastung", status: "unknown" }),
    entity({ entity_id: "sensor.gibt_es_nicht", status: "missing" }),
  ],
  ...over,
});

const overviewOf = (...connections: Connection[]): Overview => ({
  connections,
  summary: {
    connections: connections.length,
    updates: connections.reduce((n, c) => n + (c.counts?.updates ?? 0), 0),
    unavailable: connections.reduce((n, c) => n + (c.counts?.unavailable ?? 0), 0),
    unknown: connections.reduce((n, c) => n + (c.counts?.unknown ?? 0), 0),
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
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(),
    hasPermission: (p: string) => permissions.has(p),
    timezone: "Europe/Berlin",
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    urls.push(url);
    if (url.includes("/ext/home-assistant/overview")) return answer();
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

describe("Home-Assistant-Seite", () => {
  it("zeigt Version, Zähler, Updates, gewählte Werte und nicht verfügbare Entitäten", async () => {
    render(<HomeAssistantPage />);
    const card = within(await screen.findByTestId("connection-ha"));

    expect(screen.getByText("2 Updates", { selector: "span" })).toBeInTheDocument();
    expect(card.getByTestId("facts")).toHaveTextContent("Version 2026.10.1 · Standort Zuhause");
    expect(card.getByText("Entitäten").closest("div")).toHaveTextContent("20");
    expect(card.getByText("Updates", { selector: "div,p,span" }).closest("div")).toHaveTextContent("2");
    expect(card.getByText("Nicht verfügbar", { selector: "div,p,span" }).closest("div")).toHaveTextContent("4");
    expect(card.getByText("Unbekannt").closest("div")).toHaveTextContent("2");

    const updates = within(card.getByTestId("updates")).getAllByTestId("update");
    expect(updates.map((u) => within(u).getAllByText(/./)[0].textContent)).toEqual(["Home Assistant Core Update", "Mosquitto broker Update"]);
    expect(within(updates[0]).getByText("2026.10.1 → 2026.10.2")).toBeInTheDocument();
    expect(within(updates[1]).getByText("6.5.0 → 6.5.1")).toBeInTheDocument();

    const rows = within(card.getByTestId("entities")).getAllByTestId("entity");
    expect(rows).toHaveLength(5);
    expect(within(rows[0]).getByText("Wohnzimmer Temperatur")).toBeInTheDocument();
    expect(within(rows[0]).getByText("sensor.wohnzimmer_temperatur")).toBeInTheDocument();
    expect(within(rows[0]).getByText("21,5 °C")).toBeInTheDocument();
    expect(within(rows[1]).getByText("58 %")).toBeInTheDocument();
    expect(within(rows[2]).getByText("nicht verfügbar")).toBeInTheDocument();
    expect(within(rows[3]).getByText("unbekannt")).toBeInTheDocument();
    expect(within(rows[4]).getByText("sensor.gibt_es_nicht")).toBeInTheDocument();
    expect(within(rows[4]).getByText("nicht gefunden")).toBeInTheDocument();
    expect(within(rows[4]).getByText("Diese Kennung gibt es in Home Assistant nicht.")).toBeInTheDocument();

    const gone = card.getByTestId("unavailable");
    expect(gone).toHaveAttribute("open");
    expect(within(gone).getByText("4 Entitäten sind nicht verfügbar")).toBeInTheDocument();
    expect(within(gone).getAllByTestId("unavailable-item").map((i) => within(i).getAllByText(/./)[0].textContent)).toEqual([
      "Balkon Temperatur", "Fernseher", "Garten Lampe", "WLED Regal Firmware",
    ]);
    expect(screen.getByRole("link", { name: "Home Assistant öffnen" })).toHaveAttribute("href", "http://ha.example.com:8123");
    expect(urls).toEqual(["/api/v1/ext/home-assistant/overview"]);
  });

  it("wenn alles in Ordnung ist: grünes Abzeichen, keine Updates, keine Liste nicht verfügbarer Entitäten", async () => {
    answer = () => ok(overviewOf(connection({
      counts: { total: 5, updates: 0, unavailable: 0, unknown: 0 }, updates: [], unavailable: [], entities: [],
    })));
    render(<HomeAssistantPage />);
    expect(await screen.findByText("alles in Ordnung")).toBeInTheDocument();
    expect(screen.getByTestId("no-updates")).toHaveTextContent("Alles aktuell");
    expect(screen.queryByTestId("unavailable")).not.toBeInTheDocument();
    expect(screen.queryByTestId("updates")).not.toBeInTheDocument();
    expect(screen.getByTestId("entities-hint")).toHaveTextContent("trage ihre Kennungen unter Einstellungen → Erweiterungen → Home Assistant ein");
  });

  it("ohne Updates, aber mit nicht verfügbaren Entitäten steht das im Abzeichen", async () => {
    answer = () => ok(overviewOf(connection({ counts: { total: 9, updates: 0, unavailable: 3, unknown: 0 }, updates: [] })));
    render(<HomeAssistantPage />);
    expect(await screen.findByText("3 nicht verfügbar", { selector: "span" })).toBeInTheDocument();
  });

  it("eine lange Liste ist eingeklappt und sagt, wie viele nicht gezeigt werden", async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ entity_id: `sensor.weg${i}`, name: `Weg ${i}` }));
    answer = () => ok(overviewOf(connection({
      counts: { total: 300, updates: 60, unavailable: 120, unknown: 0 }, unavailable: many,
      updates: Array.from({ length: 50 }, (_, i) => ({ entity_id: `update.u${i}`, name: `Update ${i}`, installed_version: "1", latest_version: "2" })),
    })));
    render(<HomeAssistantPage />);
    const gone = await screen.findByTestId("unavailable");
    expect(gone).not.toHaveAttribute("open");
    expect(within(gone).getByText("120 Entitäten sind nicht verfügbar")).toBeInTheDocument();
    expect(within(gone).getAllByTestId("unavailable-item")).toHaveLength(50);
    expect(within(gone).getByText(/… und 70 weitere \(hier werden die ersten 50 gezeigt\)\./)).toBeInTheDocument();
    expect(screen.getByText(/… und 10 weitere \(hier werden die ersten 50 gezeigt\)\./)).toBeInTheDocument();
  });

  it("Namen aus Home Assistant erscheinen nur als Text, nie als HTML", async () => {
    answer = () => ok(overviewOf(connection({
      unavailable: [{ entity_id: "sensor.x", name: "<img src=x onerror=alert(1)>" }],
      counts: { total: 1, updates: 0, unavailable: 1, unknown: 0 }, updates: [], entities: [],
    })));
    const { container } = render(<HomeAssistantPage />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("ohne Eintrag: Hinweis mit Link zu den Einstellungen, ohne Recht der Verweis auf einen Administrator", async () => {
    answer = () => ok(overviewOf());
    const { unmount } = render(<HomeAssistantPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText("Trage Home Assistant unter Einstellungen → Erweiterungen → Home Assistant ein.")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: /Jetzt einrichten|Einstellungen/ });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/settings/extensions/home-assistant", "/settings/extensions/home-assistant"]);
    unmount();

    permissions.delete("extensions.manage");
    render(<HomeAssistantPage />);
    expect(await screen.findByText("Noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByText(/Ein Administrator kann Home Assistant/)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("eine nicht eingerichtete Verbindung sagt, was fehlt, und führt zu den Einstellungen", async () => {
    const message = "Für ha fehlt das Zugangs-Token. Unter Einstellungen → Erweiterungen → Home Assistant eintragen.";
    answer = () => ok(overviewOf(connection({
      state: "not_configured", message, version: null, location_name: null, counts: null, updates: [], unavailable: [], entities: [],
    })));
    render(<HomeAssistantPage />);
    const box = within(await screen.findByTestId("not-configured"));
    expect(box.getByText(message)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/home-assistant");
    expect(screen.getByText("nicht eingerichtet")).toBeInTheDocument();
    expect(screen.queryByText("Entitäten")).not.toBeInTheDocument();
  });

  it("nicht erreichbar: Grund, Erneut versuchen fragt ohne Zwischenspeicher nach, keine Zähler", async () => {
    const message = "Home Assistant „ha“ antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port und ob Home Assistant läuft.";
    answer = () => ok(overviewOf(connection({
      state: "unreachable", message, version: null, location_name: null, counts: null, updates: [], unavailable: [], entities: [],
    })));
    render(<HomeAssistantPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(screen.getByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.queryByText("Entitäten")).not.toBeInTheDocument();
    expect(screen.queryByTestId("no-updates")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(problem.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByTestId("updates")).toBeInTheDocument());
    expect(urls).toEqual(["/api/v1/ext/home-assistant/overview", "/api/v1/ext/home-assistant/overview?refresh=true"]);
    expect(screen.queryByTestId("problem")).not.toBeInTheDocument();
  });

  it("abgelehntes Token: Meldung und Einstellungen, aber kein Knopf zum erneuten Versuchen", async () => {
    const message = "Home Assistant „ha“: Das Zugangs-Token wurde abgelehnt (HTTP 401). Prüfe in Home Assistant unter Profil → Sicherheit …";
    answer = () => ok(overviewOf(connection({
      state: "auth_failed", message, version: null, location_name: null, counts: null, updates: [], unavailable: [], entities: [],
    })));
    render(<HomeAssistantPage />);
    const problem = within(await screen.findByTestId("problem"));
    expect(problem.getByText(message)).toBeInTheDocument();
    expect(problem.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
    expect(problem.getByRole("link", { name: "Einstellungen" })).toHaveAttribute("href", "/settings/extensions/home-assistant");
    expect(screen.getByText("Anmeldung fehlgeschlagen", { selector: "span" })).toBeInTheDocument();
  });

  it("scheitert nur die Liste, bleibt die Version stehen und der Grund dabei", async () => {
    answer = () => ok(overviewOf(connection({
      state: "error", message: "Home Assistant „ha“ meldet einen Fehler (HTTP 500).", counts: null, updates: [], unavailable: [], entities: [],
    })));
    render(<HomeAssistantPage />);
    expect(await screen.findByTestId("facts")).toHaveTextContent("Version 2026.10.1");
    expect(screen.getByTestId("problem")).toHaveTextContent("(HTTP 500)");
    expect(screen.queryByTestId("entities")).not.toBeInTheDocument();
  });

  it("ein Ladefehler ist keine leere Seite: Grund und Erneut versuchen, danach die Daten", async () => {
    answer = () => new Response(JSON.stringify({ detail: "Datenbank gesperrt." }), { status: 500 });
    render(<HomeAssistantPage />);
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Der Zustand von Home Assistant ließ sich gerade nicht laden: Datenbank gesperrt.");
    expect(screen.queryByText("Noch keine Verbindung")).not.toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();

    answer = () => ok(overviewOf(connection()));
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("connection-ha")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).not.toBeInTheDocument();
  });

  it("schlägt Aktualisieren fehl, bleiben die alten Daten stehen und der Fehler steht dabei", async () => {
    render(<HomeAssistantPage />);
    await screen.findByTestId("connection-ha");
    answer = () => new Response(JSON.stringify({ detail: "Server überlastet." }), { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Server überlastet.");
    expect(screen.getByTestId("connection-ha")).toBeInTheDocument();
    expect(urls[1]).toBe("/api/v1/ext/home-assistant/overview?refresh=true");
  });

  it("zeigt „Lade …“, solange die Antwort fehlt", async () => {
    let release: (r: Response) => void = () => {};
    answer = () => new Promise<Response>((resolve) => { release = resolve; });
    render(<HomeAssistantPage />);
    expect(screen.getByText("Lade …")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeDisabled();
    release(ok(overviewOf(connection())));
    expect(await screen.findByTestId("connection-ha")).toBeInTheDocument();
    expect(screen.queryByText("Lade …")).not.toBeInTheDocument();
  });

  it("mehrere Verbindungen: je eine Karte; Zeilen brechen um statt über den Bildschirm hinauszuragen", async () => {
    answer = () => ok(overviewOf(connection({ name: "eins" }), connection({ name: "zwei", url: "http://zwei.example.com:8123" })));
    render(<HomeAssistantPage />);
    expect(await screen.findByTestId("connection-eins")).toBeInTheDocument();
    expect(screen.getByTestId("connection-zwei")).toBeInTheDocument();
    const row = within(screen.getByTestId("connection-eins")).getAllByTestId("entity")[0];
    expect(row.className).toContain("flex-wrap");
    expect(row.innerHTML).toContain("break-words");
    expect(row.innerHTML).not.toMatch(/\bw-\[|min-w-\[|whitespace-nowrap\s+w-/);
  });

  it("ohne extensions.manage gibt es keinen Link zu den Einstellungen", async () => {
    permissions.delete("extensions.manage");
    answer = () => ok(overviewOf(connection({
      state: "error", message: "Unter dieser Adresse läuft kein Home Assistant.", version: null, location_name: null, counts: null,
      updates: [], unavailable: [], entities: [],
    })));
    render(<HomeAssistantPage />);
    expect(await screen.findByTestId("problem")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Einstellungen" })).not.toBeInTheDocument();
  });
});

describe("valueText", () => {
  it.each([
    ["21.5", "°C", "21,5 °C"],
    ["58", "%", "58 %"],
    ["on", null, "on"],
    ["-3.25", "K", "-3,25 K"],
    ["1.2.3", null, "1.2.3"],
    [null, "W", "–"],
  ])("%s %s -> %s", (state, unit, expected) => {
    expect(valueText(state, unit)).toBe(expected);
  });
});
