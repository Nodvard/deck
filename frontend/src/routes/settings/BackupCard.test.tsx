import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { browserDownload, newPasswordProblem, protectedConfigChanges, recoveryKeyFile, type BackupOverview } from "../../lib/backups";
import { useAuthStore } from "../../state/auth";
import { BackupCard } from "./BackupCard";
import { SystemSettings } from "./SystemSettings";

const RECOVERY = "AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ";

function overview(over: Partial<BackupOverview> = {}): BackupOverview {
  return {
    config: { enabled: false, schedule: "30 2 * * *", keep: 7, dir: "/app/data/backups", include_runs: false },
    key: null,
    target: {
      dir: "/app/data/backups", default_dir: "/app/data/backups", external_root: "/backups", external_available: false,
      same_storage_as_data: true, free_bytes: 5 * 1024 ** 3, error: null,
    },
    backups: [],
    last_run: null,
    running: null,
    sqlite: true,
    limits: { keep_min: 1, keep_max: 60, password_min_length: 12 },
    ...over,
  };
}

const ITEM = {
  name: "nodvard-deck-sicherung-20261001-023000.ndbak", size: 4_200_000, created_at: "2026-10-01T00:30:00Z",
  app_version: "0.5.0", key_id: "0123456789abcdef", mode: "schluessel" as const, status: "ok" as const, key_current: true,
};

type Handler = (body: unknown) => unknown;

function mockApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    const handler = routes[`${method} ${path}`];
    if (!handler) throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
    const result = handler(body);
    if (result instanceof Response) return result;
    return result === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(result), { status: 200 });
  }));
  return calls;
}

function setUser(isOwner: boolean, permissions = ["*"]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: isOwner, locale: "de", permissions },
  });
}

const typeInto = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => setUser(true));
afterEach(() => vi.restoreAllMocks());

describe("Hilfsfunktionen", () => {
  it("prüft neue Passwörter auf Länge und Wiederholung", () => {
    expect(newPasswordProblem("kurz", "kurz")).toMatch(/Mindestens 12/);
    expect(newPasswordProblem("lang-genug-123", "lang-genug-124")).toMatch(/stimmen nicht/);
    expect(newPasswordProblem("lang-genug-123", "lang-genug-123")).toBeNull();
  });

  it("erkennt Einstellungen, die das Anmeldepasswort brauchen", () => {
    const old = { enabled: true, keep: 7, dir: "/app/data/backups" };
    const def = "/app/data/backups";
    expect(protectedConfigChanges(old, { enabled: true, keep: 7, dir: def }, def)).toEqual([]);
    expect(protectedConfigChanges(old, { enabled: true, keep: 20, dir: `${def}/` }, def)).toEqual([]);
    expect(protectedConfigChanges(old, { enabled: true, keep: 7, dir: "" }, def)).toEqual([]);
    expect(protectedConfigChanges(old, { enabled: true, keep: 6, dir: def }, def)).toEqual(["weniger Sicherungen behalten"]);
    expect(protectedConfigChanges(old, { enabled: true, keep: Number.NaN, dir: def }, def)).toEqual([]);
    expect(protectedConfigChanges(old, { enabled: false, keep: 7, dir: def }, def)).toEqual(["die automatische Sicherung ausschalten"]);
    expect(protectedConfigChanges({ ...old, enabled: false }, { enabled: false, keep: 7, dir: def }, def)).toEqual([]);
    expect(protectedConfigChanges(old, { enabled: true, keep: 7, dir: "/backups//nas/" }, def)).toEqual(["den Ordner ändern"]);
    expect(protectedConfigChanges(old, { enabled: false, keep: 1, dir: "/backups" }, def)).toHaveLength(3);
  });

  it("die Schlüsseldatei enthält Schlüssel und Notfall-Anleitung", () => {
    const text = recoveryKeyFile({ key_id: "abc", recipient: "age1x", recovery_key: RECOVERY });
    expect(text).toContain(RECOVERY);
    expect(text).toContain("age -d -i");
  });
});

describe("Karte Sicherung", () => {
  it("Passwort festlegen: doppelt eingeben, Warnung bestätigen, Schlüssel nur einmal zeigen", async () => {
    let current = overview();
    const calls = mockApi({
      "GET /system/backups": () => current,
      "PUT /system/backups/key": () => {
        current = overview({ key: { key_id: "0123456789abcdef", created_at: "2026-10-01T10:00:00Z" } });
        return { key_id: "0123456789abcdef", recipient: "age1abc", recovery_key: RECOVERY };
      },
    });
    render(<BackupCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Sicherungspasswort festlegen" }));
    expect(screen.getByText("Passwort weg = Sicherungen wertlos.")).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "Passwort festlegen" });
    typeInto("Neues Sicherungspasswort", "mein-sicheres-pw-1");
    typeInto("Sicherungspasswort wiederholen", "mein-sicheres-pw-2");
    expect(screen.getByRole("alert")).toHaveTextContent("stimmen nicht überein");
    typeInto("Sicherungspasswort wiederholen", "mein-sicheres-pw-1");
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    expect(submit).toBeDisabled(); // Warnung noch nicht bestätigt
    fireEvent.click(screen.getByRole("checkbox", { name: /Ich habe verstanden/ }));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    const panel = await screen.findByTestId("recovery-key");
    expect(panel).toHaveTextContent(RECOVERY);
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ current_password: "konto-pw", password: "mein-sicheres-pw-1" });
    // Formular ist zu, Felder sind leer.
    expect(screen.queryByLabelText("Neues Sicherungspasswort")).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Ich habe ihn sicher aufbewahrt" }));
    expect(screen.queryByText(RECOVERY)).not.toBeInTheDocument();
    expect(await screen.findByText(/Eingerichtet am/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Passwort ändern" }));
    expect(screen.getByLabelText("Neues Sicherungspasswort")).toHaveValue("");
    expect(screen.queryByText(RECOVERY)).not.toBeInTheDocument();
  });

  it("zu kurzes Passwort lässt sich nicht absenden", async () => {
    mockApi({ "GET /system/backups": () => overview() });
    render(<BackupCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Sicherungspasswort festlegen" }));
    typeInto("Neues Sicherungspasswort", "kurz");
    typeInto("Sicherungspasswort wiederholen", "kurz");
    expect(screen.getByRole("alert")).toHaveTextContent("Mindestens 12 Zeichen");
    expect(screen.getByRole("button", { name: "Passwort festlegen" })).toBeDisabled();
  });

  it("warnt, wenn die Sicherung auf demselben Speicher liegt", async () => {
    mockApi({ "GET /system/backups": () => overview() });
    render(<BackupCard />);
    expect(await screen.findByText(/schützt vor Fehlbedienung, aber nicht vor einem Defekt/)).toBeInTheDocument();
  });

  it("Ordner nicht eingebunden: ein sichtbarer Satz erklärt, warum der Knopf gesperrt ist (kein bloßer Tooltip)", async () => {
    mockApi({ "GET /system/backups": () => overview() });
    render(<BackupCard />);
    const note = await screen.findByTestId("external-not-mounted");
    expect(note.textContent).toContain("/backups ist hier nicht eingebunden");
    expect(note.textContent).toContain("Compose-Datei");
    expect(screen.getByRole("button", { name: /Eingebundener Ordner \/backups/ })).toBeDisabled();
  });

  it("Ordner eingebunden: kein Erklärsatz, der Knopf geht", async () => {
    const base = overview();
    mockApi({ "GET /system/backups": () => overview({ target: { ...base.target, external_available: true } }) });
    render(<BackupCard />);
    await screen.findByRole("button", { name: /Eingebundener Ordner/ });
    expect(screen.queryByTestId("external-not-mounted")).toBeNull();
    expect(screen.getByRole("button", { name: /Eingebundener Ordner/ })).toBeEnabled();
  });

  it("ohne Schlüssel kein automatisches Sichern und kein „Jetzt sichern“", async () => {
    mockApi({ "GET /system/backups": () => overview() });
    render(<BackupCard />);
    expect(await screen.findByRole("switch", { name: "Automatisch sichern" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Jetzt sichern" })).toBeDisabled();
  });

  it("speichert Zeitplan und mehr Sicherungen ohne Passwort", async () => {
    const keyed = overview({ key: { key_id: "0123456789abcdef", created_at: null } });
    const calls = mockApi({
      "GET /system/backups": () => keyed,
      "PUT /system/backups/config": (body) => overview({ ...keyed, config: body as BackupOverview["config"] }),
    });
    render(<BackupCard />);
    fireEvent.click(await screen.findByRole("switch", { name: "Automatisch sichern" }));
    expect(screen.getByRole("group", { name: "Zeitplan der Sicherung" })).toBeInTheDocument();
    typeInto("Anzahl behalten", "61");
    expect(screen.getByRole("alert")).toHaveTextContent("zwischen 1 und 60");
    typeInto("Anzahl behalten", "14");
    expect(screen.queryByLabelText("Anmeldepasswort für die Einstellungen")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      enabled: true, schedule: "30 2 * * *", keep: 14, dir: "/app/data/backups", include_runs: false,
    });
  });

  it("anderer Ordner: Passwortfeld erscheint, Speichern erst damit, Passwort wird danach geleert", async () => {
    const keyed = overview({ key: { key_id: "0123456789abcdef", created_at: null } });
    const calls = mockApi({
      "GET /system/backups": () => keyed,
      "PUT /system/backups/config": (body) => overview({ ...keyed, config: { ...(body as BackupOverview["config"]), dir: "/backups/nas" } }),
    });
    render(<BackupCard />);
    await screen.findByRole("switch", { name: "Automatisch sichern" });
    typeInto("Ordner für Sicherungen", "/backups/nas");
    const save = screen.getByRole("button", { name: "Speichern" });
    expect(screen.getByText(/denn du willst den Ordner ändern/)).toBeInTheDocument();
    expect(save).toBeDisabled();
    typeInto("Anmeldepasswort für die Einstellungen", "konto-pw");
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await screen.findByText("Gespeichert.");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      enabled: false, schedule: "30 2 * * *", keep: 7, dir: "/backups/nas", include_runs: false, current_password: "konto-pw",
    });
    // Neuer Stand = gespeichert: kein Passwortfeld mehr.
    expect(screen.queryByLabelText("Anmeldepasswort für die Einstellungen")).not.toBeInTheDocument();
  });

  it("weniger behalten und Ausschalten verlangen das Passwort, falsches Passwort leert das Feld", async () => {
    const keyed = overview({ key: { key_id: "0123456789abcdef", created_at: null }, config: { enabled: true, schedule: "30 2 * * *", keep: 7, dir: "/app/data/backups", include_runs: false } });
    const calls = mockApi({
      "GET /system/backups": () => keyed,
      "PUT /system/backups/config": () => new Response(JSON.stringify({ detail: "Das Passwort stimmt nicht." }), { status: 400 }),
    });
    render(<BackupCard />);
    await screen.findByRole("switch", { name: "Automatisch sichern" });
    typeInto("Anzahl behalten", "3");
    expect(await screen.findByText(/weniger Sicherungen behalten/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Automatisch sichern" }));
    expect(screen.getByText(/weniger Sicherungen behalten, die automatische Sicherung ausschalten/)).toBeInTheDocument();
    typeInto("Anmeldepasswort für die Einstellungen", "falsch");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Das Passwort stimmt nicht.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ keep: 3, enabled: false, current_password: "falsch" });
    expect(screen.getByLabelText("Anmeldepasswort für die Einstellungen")).toHaveValue("");
    // Änderung zurücknehmen: Feld weg, Passwort nicht im State.
    typeInto("Anzahl behalten", "7");
    fireEvent.click(screen.getByRole("switch", { name: "Automatisch sichern" }));
    expect(screen.queryByLabelText("Anmeldepasswort für die Einstellungen")).not.toBeInTheDocument();
  });

  it("Liste: Prüfen ohne Passwort, Löschen nur mit Anmeldepasswort", async () => {
    let items = [ITEM];
    const calls = mockApi({
      "GET /system/backups": () => overview({ key: { key_id: "0123456789abcdef", created_at: null }, backups: items }),
      [`POST /system/backups/${ITEM.name}/verify`]: () => ({ ok: true, detail: "Die Datei ist unverändert." }),
      [`DELETE /system/backups/${ITEM.name}`]: () => { items = []; return undefined; },
    });
    render(<BackupCard />);
    const list = await screen.findByRole("list", { name: "Sicherungen" });
    expect(within(list).getByText("in Ordnung")).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("button", { name: "Prüfen" }));
    expect(await screen.findByText("Die Datei ist unverändert.")).toBeInTheDocument();

    fireEvent.click(within(list).getByRole("button", { name: "Löschen" }));
    expect(within(list).getAllByRole("button", { name: "Löschen" }).at(-1)).toBeDisabled();
    fireEvent.change(within(list).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "konto-pw" } });
    fireEvent.click(within(list).getAllByRole("button", { name: "Löschen" }).at(-1)!);
    expect(await screen.findByText("Sicherung gelöscht.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ current_password: "konto-pw" });
    await waitFor(() => expect(screen.getByText("Noch keine Sicherung im Zielordner.")).toBeInTheDocument());
  });

  it("Download mit Einmal-Passwort startet einen normalen Browser-Download und leert die Felder", async () => {
    const start = vi.spyOn(browserDownload, "start").mockImplementation(() => {});
    const calls = mockApi({
      "GET /system/backups": () => overview(),
      "POST /system/backups/download": () => ({
        ticket: "t1", job_id: "j1", status: "ready", filename: "nodvard-deck-sicherung-20261001-120000.ndbak", size: 1234, error: null,
        expires_in: 300, url: "/api/v1/system/backups/download/t1",
      }),
    });
    render(<BackupCard />);
    await screen.findByText("Sicherung herunterladen", { selector: "h4" });
    // Ohne Schlüssel ist nur das Einmal-Passwort möglich.
    expect(screen.getByRole("radio", { name: /Mit meinem Sicherungspasswort/ })).toBeDisabled();
    typeInto("Einmal-Passwort", "einmal-passwort-123");
    typeInto("Einmal-Passwort wiederholen", "einmal-passwort-123");
    typeInto("Anmeldepasswort für den Download", "konto-pw");
    fireEvent.click(screen.getByRole("button", { name: /Sicherung herunterladen/ }));
    await screen.findByText(/Download gestartet/);
    expect(start).toHaveBeenCalledWith("/api/v1/system/backups/download/t1", "nodvard-deck-sicherung-20261001-120000.ndbak");
    expect(calls.find((c) => c.path === "/system/backups/download")?.body).toEqual({
      current_password: "konto-pw", mode: "passwort", password: "einmal-passwort-123",
    });
    expect(screen.getByLabelText("Einmal-Passwort")).toHaveValue("");
    expect(screen.getByLabelText("Anmeldepasswort für den Download")).toHaveValue("");
  });

  const TOTP_MISSING = { detail: "Bitte gib zusätzlich den aktuellen Zwei-Faktor-Code aus deiner App ein.", code: "totp_missing" };
  const TOTP_WRONG = { detail: "Der Zwei-Faktor-Code stimmt nicht.", code: "totp_wrong" };
  const TOTP_USED = { detail: "Dieser Code wurde schon benutzt. Warte, bis die App einen neuen Code anzeigt.", code: "totp_used" };

  it("Download mit Zwei-Faktor: fragt der Server nach dem Code, erscheint das Feld und die Eingaben bleiben stehen", async () => {
    const start = vi.spyOn(browserDownload, "start").mockImplementation(() => {});
    let answers = 0;
    const calls = mockApi({
      "GET /system/backups": () => overview(),
      "POST /system/backups/download": () => {
        answers += 1;
        if (answers === 1) return new Response(JSON.stringify(TOTP_MISSING), { status: 403 });
        if (answers === 2) return new Response(JSON.stringify(TOTP_WRONG), { status: 400 });
        return {
          ticket: "t1", job_id: "j1", status: "ready", filename: "x.ndbak", size: 1234, error: null,
          expires_in: 300, url: "/api/v1/system/backups/download/t1",
        };
      },
    });
    render(<BackupCard />);
    await screen.findByText("Sicherung herunterladen", { selector: "h4" });
    expect(screen.queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
    typeInto("Einmal-Passwort", "einmal-passwort-123");
    typeInto("Einmal-Passwort wiederholen", "einmal-passwort-123");
    typeInto("Anmeldepasswort für den Download", "konto-pw");
    const submit = screen.getByRole("button", { name: /Sicherung herunterladen/ });
    fireEvent.click(submit);
    await screen.findByText(TOTP_MISSING.detail);
    expect(screen.getByLabelText("Anmeldepasswort für den Download")).toHaveValue("konto-pw");
    expect(screen.getByLabelText("Einmal-Passwort")).toHaveValue("einmal-passwort-123");
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "000000" } });
    fireEvent.click(submit);
    // Vertippt: Nur der Code muss neu, die Passwörter bleiben stehen (das Anmeldepasswort stimmte ja).
    await screen.findByText(TOTP_WRONG.detail);
    expect(screen.getByLabelText(/^Zwei-Faktor-Code/)).toHaveValue("");
    expect(screen.getByLabelText("Anmeldepasswort für den Download")).toHaveValue("konto-pw");
    expect(screen.getByLabelText("Einmal-Passwort")).toHaveValue("einmal-passwort-123");
    expect(screen.getByLabelText("Einmal-Passwort wiederholen")).toHaveValue("einmal-passwort-123");
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Zwei-Faktor-Code/), { target: { value: " 123456 " } });
    fireEvent.click(submit);
    await screen.findByText(/Download gestartet/);
    expect(start).toHaveBeenCalledWith("/api/v1/system/backups/download/t1", "x.ndbak");
    expect(calls.filter((c) => c.path === "/system/backups/download").map((c) => c.body)).toEqual([
      { current_password: "konto-pw", mode: "passwort", password: "einmal-passwort-123" },
      { current_password: "konto-pw", mode: "passwort", password: "einmal-passwort-123", totp_code: "000000" },
      { current_password: "konto-pw", mode: "passwort", password: "einmal-passwort-123", totp_code: "123456" },
    ]);
    expect(screen.getByLabelText(/^Zwei-Faktor-Code/)).toHaveValue("");
    expect(screen.getByLabelText("Anmeldepasswort für den Download")).toHaveValue("");
  });

  it("Zwei-Faktor an (GET /me): das Code-Feld steht gleich da, ohne Rückfrage des Servers", async () => {
    const start = vi.spyOn(browserDownload, "start").mockImplementation(() => {});
    const calls = mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", totp_enabled: true }),
      "GET /system/backups": () => overview({ key: { key_id: "0123456789abcdef", created_at: null }, backups: [ITEM] }),
      "POST /system/backups/download": () => ({
        ticket: "t1", job_id: "j1", status: "ready", filename: "x.ndbak", size: 1234, error: null,
        expires_in: 300, url: "/api/v1/system/backups/download/t1",
      }),
      [`POST /system/backups/${ITEM.name}/ticket`]: () => (
        { ticket: "t2", job_id: "j2", status: "ready", filename: ITEM.name, size: 1, error: null, expires_in: 300, url: "/api/v1/system/backups/download/t2" }
      ),
    });
    render(<BackupCard />);
    // Frische Sicherung: Feld gleich da, Knopf erst mit Code.
    const field = await screen.findByLabelText(/^Zwei-Faktor-Code/);
    typeInto("Anmeldepasswort für den Download", "konto-pw");
    const submit = screen.getByRole("button", { name: /Sicherung herunterladen/ });
    expect(submit).toBeDisabled();
    fireEvent.change(field, { target: { value: "123456" } });
    fireEvent.click(submit);
    await screen.findByText(/Download gestartet/);
    expect(calls.filter((c) => c.path === "/system/backups/download").map((c) => c.body)).toEqual([
      { current_password: "konto-pw", mode: "schluessel", password: null, totp_code: "123456" },
    ]);

    // Gespeicherte Sicherung: ebenso gleich mit Code, beim Löschen ohne.
    const list = screen.getByRole("list", { name: "Sicherungen" });
    fireEvent.click(within(list).getByRole("button", { name: "Herunterladen" }));
    expect(within(list).getByLabelText(/^Zwei-Faktor-Code/)).toHaveValue("");
    fireEvent.change(within(list).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "konto-pw" } });
    const confirm = within(list).getAllByRole("button", { name: "Herunterladen" }).at(-1)!;
    expect(confirm).toBeDisabled();
    fireEvent.change(within(list).getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "654321" } });
    fireEvent.click(confirm);
    await screen.findByText("Download gestartet.");
    expect(calls.filter((c) => c.path.endsWith("/ticket")).map((c) => c.body)).toEqual([
      { current_password: "konto-pw", totp_code: "654321" },
    ]);
    expect(start).toHaveBeenCalledTimes(2);
    fireEvent.click(within(list).getByRole("button", { name: "Löschen" }));
    expect(within(list).queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
  });

  it("Zwei-Faktor aus (GET /me): kein Code-Feld; fragt der Server trotzdem, erscheint es", async () => {
    let answers = 0;
    mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", totp_enabled: false }),
      "GET /system/backups": () => overview(),
      "POST /system/backups/download": () => {
        answers += 1;
        return new Response(JSON.stringify(TOTP_MISSING), { status: 403 });
      },
    });
    render(<BackupCard />);
    await screen.findByText("Sicherung herunterladen", { selector: "h4" });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).endsWith("/me"))).toBe(true));
    expect(screen.queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
    typeInto("Einmal-Passwort", "einmal-passwort-123");
    typeInto("Einmal-Passwort wiederholen", "einmal-passwort-123");
    typeInto("Anmeldepasswort für den Download", "konto-pw");
    fireEvent.click(screen.getByRole("button", { name: /Sicherung herunterladen/ }));
    await screen.findByText(TOTP_MISSING.detail);
    expect(answers).toBe(1);
    expect(screen.getByLabelText(/^Zwei-Faktor-Code/)).toBeInTheDocument();
  });

  it("gespeicherte Sicherung mit Zwei-Faktor: Code-Feld nach der Rückfrage, nicht beim Löschen", async () => {
    const start = vi.spyOn(browserDownload, "start").mockImplementation(() => {});
    let answers = 0;
    const calls = mockApi({
      "GET /system/backups": () => overview({ key: { key_id: "0123456789abcdef", created_at: null }, backups: [ITEM] }),
      [`DELETE /system/backups/${ITEM.name}`]: () => undefined,
      [`POST /system/backups/${ITEM.name}/ticket`]: () => {
        answers += 1;
        if (answers === 1) return new Response(JSON.stringify(TOTP_MISSING), { status: 403 });
        if (answers === 2) return new Response(JSON.stringify(TOTP_USED), { status: 400 });
        return { ticket: "t2", job_id: "j2", status: "ready", filename: ITEM.name, size: 1, error: null, expires_in: 300, url: "/api/v1/system/backups/download/t2" };
      },
    });
    render(<BackupCard />);
    const list = await screen.findByRole("list", { name: "Sicherungen" });
    fireEvent.click(within(list).getByRole("button", { name: "Herunterladen" }));
    fireEvent.change(within(list).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "konto-pw" } });
    fireEvent.click(within(list).getAllByRole("button", { name: "Herunterladen" }).at(-1)!);
    await screen.findByText(TOTP_MISSING.detail);
    expect(within(list).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("konto-pw");
    const confirm = within(list).getAllByRole("button", { name: "Herunterladen" }).at(-1)!;
    expect(confirm).toBeDisabled();
    fireEvent.change(within(list).getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "123456" } });
    fireEvent.click(confirm);
    // Schon benutzt: Das Passwort bleibt stehen, nur ein neuer Code fehlt.
    await screen.findByText(TOTP_USED.detail);
    expect(within(list).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("konto-pw");
    expect(within(list).getByLabelText(/^Zwei-Faktor-Code/)).toHaveValue("");
    expect(confirm).toBeDisabled();
    fireEvent.change(within(list).getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "654321" } });
    fireEvent.click(confirm);
    await screen.findByText("Download gestartet.");
    expect(start).toHaveBeenCalledWith("/api/v1/system/backups/download/t2", ITEM.name);
    expect(calls.filter((c) => c.path.endsWith("/ticket")).map((c) => c.body)).toEqual([
      { current_password: "konto-pw" },
      { current_password: "konto-pw", totp_code: "123456" },
      { current_password: "konto-pw", totp_code: "654321" },
    ]);
    // Löschen braucht keinen Code: dort gibt es das Feld nicht, und das Passwort allein reicht für den Knopf.
    fireEvent.click(within(list).getByRole("button", { name: "Löschen" }));
    expect(within(list).queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
    fireEvent.change(within(list).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "konto-pw" } });
    const remove = within(list).getAllByRole("button", { name: "Löschen" }).at(-1)!;
    expect(remove).toBeEnabled();
    fireEvent.click(remove);
    await screen.findByText("Sicherung gelöscht.");
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ current_password: "konto-pw" });
  });

  it("Download wartet über die Status-ID, nicht über das Ticket", async () => {
    const start = vi.spyOn(browserDownload, "start").mockImplementation(() => {});
    const calls = mockApi({
      "GET /system/backups": () => overview(),
      "POST /system/backups/download": () => ({
        ticket: "geheimes-ticket", job_id: "status-id", status: "building", filename: "x.ndbak", size: null, error: null,
        expires_in: 300, url: "/api/v1/system/backups/download/geheimes-ticket",
      }),
      "GET /system/backups/download-jobs/status-id": () => ({
        ticket: "geheimes-ticket", job_id: "status-id", status: "ready", filename: "x.ndbak", size: 99, error: null,
        expires_in: 300, url: "/api/v1/system/backups/download/geheimes-ticket",
      }),
    });
    render(<BackupCard />);
    await screen.findByText("Sicherung herunterladen", { selector: "h4" });
    typeInto("Einmal-Passwort", "einmal-passwort-123");
    typeInto("Einmal-Passwort wiederholen", "einmal-passwort-123");
    typeInto("Anmeldepasswort für den Download", "konto-pw");
    fireEvent.click(screen.getByRole("button", { name: /Sicherung herunterladen/ }));
    await screen.findByText(/Download gestartet/, undefined, { timeout: 4000 });
    expect(start).toHaveBeenCalledWith("/api/v1/system/backups/download/geheimes-ticket", "x.ndbak");
    const polled = calls.filter((c) => c.method === "GET" && c.path.includes("download")).map((c) => c.path);
    expect(polled).toEqual(["/system/backups/download-jobs/status-id"]);
    expect(polled.join()).not.toContain("geheimes-ticket");
  });

  it("zeigt Hinweise zur letzten Sicherung, auch wenn sie gelang", async () => {
    const text = "2 Verknüpfungen in Erweiterungsdaten nicht gesichert: ext/a/link, ext/b/link";
    mockApi({
      "GET /system/backups": () => overview({
        key: { key_id: "0123456789abcdef", created_at: null },
        last_run: { at: "2026-10-01T00:30:00Z", ok: true, trigger: "schedule", name: ITEM.name, size: 1, error: null, warnings: [text] },
      }),
    });
    render(<BackupCard />);
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByText("erfolgreich")).toBeInTheDocument();
  });

  it("alte Einträge ohne Hinweise zeigen keine Warnung", async () => {
    mockApi({
      "GET /system/backups": () => overview({
        last_run: { at: "2026-10-01T00:30:00Z", ok: true, trigger: "schedule", name: ITEM.name, size: 1, error: null },
      }),
    });
    render(<BackupCard />);
    expect(await screen.findByText("erfolgreich")).toBeInTheDocument();
    expect(screen.queryByText(/nicht gesichert/)).not.toBeInTheDocument();
  });

  it("ohne Owner: nur ansehen", async () => {
    setUser(false, ["system.read"]);
    mockApi({ "GET /system/backups": () => overview({ key: { key_id: "0123456789abcdef", created_at: null }, backups: [ITEM] }) });
    render(<BackupCard />);
    expect(await screen.findByText(/kann nur der Inhaber/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt sichern" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Löschen" })).not.toBeInTheDocument();
    expect(screen.queryByText("Sicherung herunterladen", { selector: "h4" })).not.toBeInTheDocument();
  });
});

describe("Reiter System", () => {
  it("zeigt mit system.read Sicherung und Wiederherstellen, ohne GET /settings", async () => {
    setUser(false, ["system.read"]);
    const calls = mockApi({
      "GET /system/backups": () => overview(),
      "GET /system/restore/status": () => ({ pending: null, staged: null, result: null, replaced: null, busy: false, limits: { max_upload_bytes: 1, max_unpacked_bytes: 1, expires_in: 3600 } }),
    });
    render(<SystemSettings />);
    expect(await screen.findByText("Sicherung", { selector: "h3" })).toBeInTheDocument();
    expect(await screen.findByText("Wiederherstellen", { selector: "h3" })).toBeInTheDocument();
    expect(screen.queryByText("Zeit & Protokoll")).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === "/settings")).toBe(false);
  });
});
