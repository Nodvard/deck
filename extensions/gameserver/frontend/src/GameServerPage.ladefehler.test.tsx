import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GameServerPage } from "./GameServerPage";

/** Seiten-Durchgang: Ladefehler mit Grund, und ohne geladene Einstellung kein Speichern mit leerem Profil. */

const SUMMARY = {
  host_id: "h1", name: "game-win", status: "up", service_label: "läuft", tone: "good", running: true,
  players_online: 0, version: null, error: null, can_start: false, can_stop: true, join_code: null, join_code_display: "",
};
const DETAILS = { ...SUMMARY, details: { backups: [] }, config: { profile: "valheim-windows", values: {} } };
const PROFILES = [{ id: "valheim-windows", label: "Valheim (Windows-Dienst)", fields: [] }];

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const fail = (detail: string) => new Response(JSON.stringify({ detail }), { status: 500 });

let broken: Set<string>;
let calls: { url: string; method: string }[];

beforeEach(() => {
  broken = new Set();
  calls = [];
  window.history.replaceState({}, "", "/ext/gameserver/gameserver");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url.endsWith("/ext/gameserver/servers") && method === "GET") return broken.has("list") ? fail("Datenbank gerade gesperrt.") : ok([SUMMARY]);
    if (url.includes("/ext/gameserver/servers/h1/details") && method === "GET") return broken.has("details") ? fail("SSH-Zeitüberschreitung.") : ok(DETAILS);
    if (url.endsWith("/ext/gameserver/profiles")) return broken.has("profiles") ? fail("Profile nicht lesbar.") : ok(PROFILES);
    if (method === "POST" && url.endsWith("/servers/h1/backup")) return ok({ action_id: "a1", status: "proposed", risk: "low" });
    if (url.endsWith("/actions/a1/approve")) return ok({ id: "a1", status: "succeeded" });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
});

describe("GameServerPage bei Ladefehlern", () => {
  it("Server-Liste: Grund statt „Fehler: HTTP 500“, erneut versuchen", async () => {
    broken.add("list");
    render(<GameServerPage />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Liste der Gameserver ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    broken.clear();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByTestId("server-h1")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });

  it("scheitert das Neuladen, bleiben die Server stehen und darüber steht der Grund", async () => {
    render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    broken.add("list");
    fireEvent.click(within(card).getByRole("button", { name: "Welt sichern" }));
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Liste der Gameserver ließ sich gerade nicht laden");
    expect(screen.getByTestId("server-h1")).toBeInTheDocument();
  });

  it("Details nicht ladbar: Grund am Server, keine Einstellungen (sonst würde ein leeres Profil gespeichert)", async () => {
    broken.add("details");
    render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    expect(within(card).getByTestId("load-error")).toHaveTextContent("Der Stand dieses Servers ließ sich gerade nicht laden: SSH-Zeitüberschreitung.");
    expect(within(card).queryByRole("button", { name: "Einstellungen" })).toBeNull();
    // Ohne Details keine geratenen Leerzustände.
    expect(within(card).queryByText("Noch niemand.")).toBeNull();
    expect(within(card).queryByText(/Noch keine – „Welt sichern“/)).toBeNull();
    expect(within(card).queryByText("läuft nicht")).toBeNull();
    broken.clear();
    fireEvent.click(within(card).getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(within(screen.getByTestId("server-h1")).queryByTestId("load-error")).toBeNull());
    expect(within(screen.getByTestId("server-h1")).getByRole("button", { name: "Einstellungen" })).toBeInTheDocument();
  });

  it("Spiel-Profile nicht ladbar: die Einstellungen sagen es und bieten kein Speichern an", async () => {
    broken.add("profiles");
    render(<GameServerPage />);
    fireEvent.click(within(await screen.findByTestId("server-h1")).getByRole("button", { name: "Einstellungen" }));
    const panel = await screen.findByTestId("settings-h1");
    expect(within(panel).getByTestId("load-error")).toHaveTextContent("Die Liste der Spiel-Profile ließ sich gerade nicht laden: Profile nicht lesbar.");
    expect(within(panel).queryByRole("button", { name: "Speichern" })).toBeNull();
    broken.clear();
    fireEvent.click(within(panel).getByRole("button", { name: "Erneut versuchen" }));
    expect(await within(screen.getByTestId("settings-h1")).findByRole("button", { name: "Speichern" })).toBeInTheDocument();
  });
});

describe("GameServerPage beim Neuladen", () => {
  it("scheitert nur der Stand eines Servers, bleibt der letzte gute samt offener Einstellungen", async () => {
    render(<GameServerPage />);
    const card = await screen.findByTestId("server-h1");
    fireEvent.click(within(card).getByRole("button", { name: "Einstellungen" }));
    await screen.findByTestId("settings-h1");
    broken.add("details");
    fireEvent.click(within(card).getByRole("button", { name: "Welt sichern" }));
    expect(await screen.findByTestId("stale-details")).toHaveTextContent("Neu laden ging gerade nicht (SSH-Zeitüberschreitung.)");
    expect(screen.getByTestId("settings-h1")).toBeInTheDocument();
    expect(within(screen.getByTestId("server-h1")).queryByTestId("load-error")).toBeNull();
  });
});
