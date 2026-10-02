import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { SystemSettings } from "./SystemSettings";
import { UpdateCopiesCard, type PreUpdateCopy } from "./UpdateCopiesCard";

const COPY: PreUpdateCopy = {
  name: "20261001T030000Z_0.6.0_0.7.0.db", created_at: "2026-10-01T03:00:00Z", from_version: "0.6.0", to_version: "0.7.0", size: 12_000_000,
};

function mockInfo(respond: () => Response | Promise<Response>) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    calls.push(path);
    if (path !== "/system/info") throw new Error(`Unerwarteter Fetch: ${path}`);
    return respond();
  }));
  return calls;
}

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["system.read"] },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("Karte Kopien vor Updates", () => {
  it("zeigt die letzten Kopien mit Versionen, Zeit und Größe", async () => {
    mockInfo(() => json({ version: "0.7.0", pre_update_copies: [COPY, { ...COPY, name: "20260901T030000Z_0.5.0_0.6.0.db", from_version: "0.5.0", to_version: "0.6.0", created_at: "2026-09-01T03:00:00Z" }] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText("Kopien vor Updates", { selector: "h3" })).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Update von Version 0.6.0 auf 0.7.0");
    expect(rows[0]).toHaveTextContent("11 MB");
    expect(rows[0]).toHaveTextContent("backups/vor-update/20261001T030000Z_0.6.0_0.7.0.db");
    expect(rows[1]).toHaveTextContent("Update von Version 0.5.0 auf 0.6.0");
  });

  it("sagt ehrlich, wann der Rückweg klappt", async () => {
    mockInfo(() => json({ pre_update_copies: [COPY] }));
    render(<UpdateCopiesCard />);
    await screen.findByText("Kopien vor Updates", { selector: "h3" });
    expect(screen.getByText(/spielt Nodvard Deck die Kopie von selbst wieder ein/)).toBeInTheDocument();
    expect(screen.getByText(/Notfallcode steht im Protokoll des Containers/)).toBeInTheDocument();
    expect(screen.getByText(/Beides klappt erst ab einer Version, die diese Kopien schon anlegt/)).toBeInTheDocument();
  });

  it("bei einer anderen Datenbank als SQLite: keine Kopien versprechen, wie die Karte Updates", async () => {
    mockInfo(() => json({ database: "postgresql", pre_update_copies: [] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText("Kopien vor Updates", { selector: "h3" })).toBeInTheDocument();
    expect(screen.getByText(/Bei dieser Datenbank \(postgresql\) legt Nodvard Deck vor einem Update keine Kopie an/)).toBeInTheDocument();
    expect(screen.getByText(/Bitte sichere die Datenbank vor einem Update selbst/)).toBeInTheDocument();
    expect(screen.queryByText(/Noch keine Kopie/)).not.toBeInTheDocument();
    expect(screen.queryByText(/spielt Nodvard Deck die Kopie von selbst wieder ein/)).not.toBeInTheDocument();
  });

  it("bei SQLite (ausdrücklich genannt) bleibt es bei den Kopien", async () => {
    mockInfo(() => json({ database: "sqlite", pre_update_copies: [] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText(/Noch keine Kopie/)).toBeInTheDocument();
  });

  it("ohne Kopie steht da, wann die erste entsteht", async () => {
    mockInfo(() => json({ pre_update_copies: [] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText(/Noch keine Kopie/)).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("kennt eine Kopie ohne Versionsangabe", async () => {
    mockInfo(() => json({ pre_update_copies: [{ ...COPY, from_version: null, to_version: null }] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText("Datenbank-Umbau (Version nicht bekannt)")).toBeInTheDocument();
  });

  it("gleiche Version vorher und nachher: Datenbank-Umbau statt „Update von 0.5.0 auf 0.5.0“", async () => {
    mockInfo(() => json({ pre_update_copies: [{ ...COPY, from_version: "0.5.0", to_version: "0.5.0" }] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText("Datenbank-Umbau in 0.5.0")).toBeInTheDocument();
    expect(screen.queryByText(/auf 0\.5\.0/)).not.toBeInTheDocument();
  });

  it("kennt nur eine der beiden Versionen", async () => {
    mockInfo(() => json({ pre_update_copies: [
      { ...COPY, name: "a.db", from_version: null, to_version: "0.7.0" },
      { ...COPY, name: "b.db", from_version: "0.6.0", to_version: null },
    ] }));
    render(<UpdateCopiesCard />);
    expect(await screen.findByText("Update von früherer Version auf 0.7.0")).toBeInTheDocument();
    expect(screen.getByText("Update von Version 0.6.0")).toBeInTheDocument();
  });

  it("zeigt bei einem älteren Server (ohne das Feld) oder einem Fehler gar nichts", async () => {
    mockInfo(() => json({ version: "0.5.0" }));
    const first = render(<UpdateCopiesCard />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(first.container).toBeEmptyDOMElement();
    first.unmount();
    mockInfo(() => new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 }));
    const second = render(<UpdateCopiesCard />);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(second.container).toBeEmptyDOMElement();
  });

  it("gehört mit system.read zum Reiter System, ohne das Recht nicht", async () => {
    // Nur das Recht dieser Karte: die anderen Karten würden eigene Abfragen stellen.
    useAuthStore.setState({ user: { id: "u1", username: "x", display_name: "x", email: null, is_owner: false, locale: "de", permissions: ["settings.write"] } });
    const calls = mockInfo(() => json({ pre_update_copies: [COPY] }));
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
      calls.push(path);
      return path === "/settings" ? json([{ key: "system.timezone", value: "Europe/Berlin" }, { key: "audit.retention_days", value: 90 }]) : json({ pre_update_copies: [COPY] });
    }));
    render(<SystemSettings />);
    await screen.findByText("Zeit & Protokoll");
    expect(screen.queryByText("Kopien vor Updates")).not.toBeInTheDocument();
    expect(calls).not.toContain("/system/info");
  });
});
