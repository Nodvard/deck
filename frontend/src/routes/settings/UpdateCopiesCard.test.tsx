import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetUpdateHelper, type HelperView } from "../../lib/updater";
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
  resetUpdateHelper();
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["system.read"] },
  });
});
afterEach(() => {
  resetUpdateHelper();
  vi.unstubAllGlobals();
});

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

// ---------------------------------------------------------------------------
// Rückweg über den Update-Helfer
// ---------------------------------------------------------------------------

const REQUEST_ID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60";
const SINCE = Date.UTC(2026, 9, 1, 3, 0, 0) / 1000;

function helperView(previous: HelperView["previous"], patch: Partial<HelperView> = {}): HelperView {
  return {
    present: true, reason: null, ready: true, ready_reason: null, state: "idle", helper_version: "0.7.1",
    heartbeat_at: Math.floor(Date.now() / 1000), target: { current_version: "0.7.1", floating_tag: "latest", pinned: false },
    busy: null, previous, last_result: null, pending: null, ...patch,
  };
}

const future = () => Math.floor(Date.now() / 1000) + 3 * 24 * 3600;

function mockAll(view: HelperView, rollback: (body: unknown) => Response = () => json({})) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    if (path === "/system/info") return json({ database: "sqlite", pre_update_copies: [COPY] });
    if (path === "/system/updates/helper") return json(view);
    if (method === "POST" && path === "/system/updates/rollback") return rollback(body);
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  }));
  return calls;
}

const reply = (value: unknown, status: number) => new Response(JSON.stringify(value), { status });

function setOwner() {
  useAuthStore.setState({ user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] } });
}

describe("Karte Kopien vor Updates: Rückweg", () => {
  it("mit Daten: warnt mit dem Zeitpunkt, verlangt Häkchen und Passwort und folgt danach dem Rückweg", async () => {
    setOwner();
    const calls = mockAll(helperView({ version: "0.7.0", until: future(), data_revert: true, data_since: SINCE }),
      () => reply({ request_id: REQUEST_ID, action: "rollback", from: "0.7.1", to: "0.7.0", data_revert: true }, 202));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    const since = new Date(SINCE * 1000).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
    expect(within(section).getByTestId("rollback-data-warning")).toHaveTextContent(`Änderungen seit ${since} gehen verloren`);
    expect(section).toHaveTextContent("restore/replaced-…");

    fireEvent.click(within(section).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const form = within(section).getByRole("form", { name: "Rückweg bestätigen" });
    const submit = within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    expect(submit).toBeDisabled();
    fireEvent.click(within(form).getByRole("checkbox", { name: `Ich weiß: Änderungen seit ${since} gehen verloren.` }));
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    const progress = await screen.findByTestId("helper-progress");
    expect(progress).toHaveTextContent("Zurück zu Version 0.7.0");
    expect(screen.queryByTestId("rollback-section")).not.toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ current_password: "geheim", accept_data_loss: true });
  });

  it("ohne Umbau der Datenbank: keine Warnung, kein Häkchen, die Daten bleiben", async () => {
    setOwner();
    const calls = mockAll(helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null }),
      () => reply({ request_id: REQUEST_ID, action: "rollback", from: "0.7.1", to: "0.7.0", data_revert: false }, 202));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    expect(section).toHaveTextContent("Deine Daten bleiben dabei, wie sie sind");
    expect(within(section).queryByTestId("rollback-data-warning")).not.toBeInTheDocument();
    fireEvent.click(within(section).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const form = within(section).getByRole("form", { name: "Rückweg bestätigen" });
    expect(within(form).queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    await screen.findByTestId("helper-progress");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ current_password: "geheim", accept_data_loss: false });
  });

  it("fragt der Server doch nach der Zustimmung, erscheint das Häkchen; das Passwort bleibt stehen", async () => {
    setOwner();
    const answers = [
      () => reply({ detail: "Beim Rückweg geht alles verloren, was seit dem Update geändert wurde. Bitte bestätige das.", code: "accept_data_loss" }, 422),
      () => reply({ request_id: REQUEST_ID, action: "rollback", from: "0.7.1", to: "0.7.0", data_revert: true }, 202),
    ];
    const calls = mockAll(helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null }), () => answers.shift()!());
    render(<UpdateCopiesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const form = screen.getByRole("form", { name: "Rückweg bestätigen" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const box = await within(form).findByRole("checkbox", { name: "Ich weiß: Änderungen seit dem Update gehen verloren." });
    expect(within(form).getByRole("alert")).toHaveTextContent("Bitte bestätige das.");
    expect(within(form).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("geheim");
    fireEvent.click(box);
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    await screen.findByTestId("helper-progress");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
      { current_password: "geheim", accept_data_loss: false },
      { current_password: "geheim", accept_data_loss: true },
    ]);
  });

  it("mit Zwei-Faktor: Codefeld nach totp_missing, der Code geht mit", async () => {
    setOwner();
    const answers = [
      () => reply({ detail: "Bitte gib zusätzlich den Code aus deiner Authenticator-App ein.", code: "totp_missing" }, 403),
      () => reply({ request_id: REQUEST_ID, action: "rollback", from: "0.7.1", to: "0.7.0", data_revert: false }, 202),
    ];
    const calls = mockAll(helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null }), () => answers.shift()!());
    render(<UpdateCopiesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const form = screen.getByRole("form", { name: "Rückweg bestätigen" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    fireEvent.change(await within(form).findByLabelText(/^Zwei-Faktor-Code/), { target: { value: "123456" } });
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    await screen.findByTestId("helper-progress");
    expect(calls.filter((c) => c.method === "POST").at(-1)?.body).toEqual({ current_password: "geheim", accept_data_loss: false, totp_code: "123456" });
  });

  it("unklare Daten: erklärt es vorsichtig, verspricht keinen Rückweg und bietet keinen Knopf", async () => {
    setOwner();
    mockAll(helperView({ version: "0.7.0", until: future(), data_revert: null, data_since: null }));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    expect(within(section).getByRole("heading")).toHaveTextContent("Rückweg zu Version 0.7.0");
    expect(section).not.toHaveTextContent("kann bis");
    expect(section).not.toHaveTextContent("Zurück zu Version");
    expect(within(section).getByTestId("rollback-unclear")).toHaveTextContent("Ob deine Daten beim Rückweg mit zurückmüssen, lässt sich nicht sicher sagen");
    expect(section).toHaveTextContent("Per Knopf geht es deshalb nicht zurück");
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });

  it("lehnt der Server mit data_unclear ab, liest die Karte den Zustand neu und erklärt es", async () => {
    setOwner();
    let view = helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
      if (path === "/system/info") return json({ database: "sqlite", pre_update_copies: [COPY] });
      if (path === "/system/updates/helper") return json(view);
      if (init?.method === "POST") {
        view = helperView({ version: "0.7.0", until: future(), data_revert: null, data_since: null });
        return reply({ detail: "Ein Rückweg über den Update-Helfer ist deshalb nicht möglich.", code: "data_unclear" }, 409);
      }
      throw new Error(`Unerwarteter Fetch: ${path}`);
    }));
    render(<UpdateCopiesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const form = screen.getByRole("form", { name: "Rückweg bestätigen" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    expect(await screen.findByTestId("rollback-unclear")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Zurück zu Version/ })).not.toBeInTheDocument();
  });

  it("klare Daten: sagt, bis wann der Rückweg geht", async () => {
    setOwner();
    mockAll(helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null }));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    expect(within(section).getByRole("heading")).toHaveTextContent("Zurück zu Version 0.7.0");
    expect(section).toHaveTextContent("Der Update-Helfer kann bis");
  });

  it("Rückweg mit Daten scheitert: das Ergebnis sagt, wo steht, was mit den Daten geschah", async () => {
    setOwner();
    let view = helperView({ version: "0.7.0", until: future(), data_revert: true, data_since: SINCE });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
      if (path === "/system/info") return json({ database: "sqlite", pre_update_copies: [COPY] });
      if (path === "/system/updates/helper") return json(view);
      if (init?.method === "POST") {
        view = helperView(null, {
          last_result: { id: REQUEST_ID, action: "rollback", from: "0.7.1", to: "0.7.0", outcome: "rolled_back", code: "rescue_page", finished_at: Math.floor(Date.now() / 1000) },
        });
        return reply({ request_id: REQUEST_ID, action: "rollback", from: "0.7.1", to: "0.7.0", data_revert: true }, 202);
      }
      throw new Error(`Unerwarteter Fetch: ${path}`);
    }));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    fireEvent.click(within(section).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const form = within(section).getByRole("form", { name: "Rückweg bestätigen" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(within(form).getByRole("checkbox"));
    fireEvent.click(within(form).getByRole("button", { name: "Zurück zu Version 0.7.0" }));
    const box = await screen.findByTestId("helper-result");
    expect(box).toHaveAttribute("data-outcome", "rolled_back");
    expect(box).toHaveTextContent("Der Rückweg auf 0.7.0 hat nicht geklappt");
    expect(box).toHaveTextContent("steht in der Meldung dazu (Glocke)");
  });

  it("nur der Owner bekommt den Knopf; andere sehen, dass es den Rückweg gibt", async () => {
    mockAll(helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null }));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    expect(section).toHaveTextContent("Zurückschalten kann nur der Inhaber");
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });

  it("Helfer nicht bereit: kein Knopf, aber der Hinweis", async () => {
    setOwner();
    mockAll(helperView({ version: "0.7.0", until: future(), data_revert: false, data_since: null }, { ready: false, ready_reason: "target_unhealthy" }));
    render(<UpdateCopiesCard />);
    const section = await screen.findByTestId("rollback-section");
    expect(section).toHaveTextContent("sobald der Update-Helfer bereit ist");
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });

  it("ohne gültigen Rückweg (abgelaufen oder keiner) gibt es keinen Abschnitt", async () => {
    setOwner();
    mockAll(helperView({ version: "0.7.0", until: Math.floor(Date.now() / 1000) - 5, data_revert: false, data_since: null }));
    const first = render(<UpdateCopiesCard />);
    await screen.findByText("Kopien vor Updates", { selector: "h3" });
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/system/updates/helper"))).toBe(true));
    expect(screen.queryByTestId("rollback-section")).not.toBeInTheDocument();
    first.unmount();
    resetUpdateHelper();
    mockAll(helperView(null));
    render(<UpdateCopiesCard />);
    await screen.findByText("Kopien vor Updates", { selector: "h3" });
    expect(screen.queryByTestId("rollback-section")).not.toBeInTheDocument();
  });
});
