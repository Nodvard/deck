import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import {
  browserNavigation,
  restartProbe,
  restartTiming,
  uploadTransport,
  type RestoreStatus,
  type RestoreSummary,
  type StagedRestore,
} from "../../lib/restore";
import { useAuthStore } from "../../state/auth";
import { RestoreCard } from "./RestoreCard";
import { SystemSettings } from "./SystemSettings";

const ID = "a".repeat(32);

const SUMMARY: RestoreSummary = {
  created_at: "2026-10-01T03:00:00Z",
  app_version: "0.5.0",
  instance_id: "0123456789abcdef0123456789abcdef",
  mode: "passwort",
  owner_name: "anna",
  users: 3,
  hosts: 7,
  extensions: [{ id: "proxmox", version: "1.2.0" }, { id: "backups", version: "0.4.0" }],
  includes: { runs: false, branding: true, jwt_secret: true },
  warnings: ["Die Sicherung stammt von einer anderen Installation als dieser. Nur einspielen, wenn die Sicherung selbst erstellt wurde."],
};

function staged(over: Partial<StagedRestore> = {}): StagedRestore {
  return {
    id: ID, state: "uploaded", size: 4_200_000,
    header: { mode: "passwort", created_at: "2026-10-01T03:00:00Z", app_version: "0.5.0" },
    summary: null, expires_in: 3500, ...over,
  };
}

function status(over: Partial<RestoreStatus> = {}): RestoreStatus {
  return {
    pending: null, staged: null, result: null, replaced: null, busy: false,
    limits: { max_upload_bytes: 4 * 1024 ** 3, max_unpacked_bytes: 16 * 1024 ** 3, expires_in: 3600 },
    ...over,
  };
}

type Call = { method: string; path: string; body: unknown; headers: Record<string, string> };
type Handler = (call: Call) => unknown;

/** Attrappen-Server: antwortet je Route (`"POST /system/restart"`), merkt sich Aufrufe samt Kopfzeilen. */
function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((value, name) => { headers[name.toLowerCase()] = value; });
    const call: Call = {
      method: init?.method ?? "GET", path: String(input).replace(/^\/api\/v1/, "").split("?")[0], headers,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    const handler = routes[`${call.method} ${call.path}`];
    if (!handler) throw new Error(`Unerwarteter Fetch: ${call.method} ${call.path}`);
    const result = handler(call);
    if (result instanceof Response) return result;
    return result === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(result), { status: 200 });
  }));
  return calls;
}

const fail = (status: number, detail: string) => new Response(JSON.stringify({ detail }), { status });

function setUser(isOwner: boolean, permissions = ["*"]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: isOwner, locale: "de", permissions },
  });
}

const typeInto = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const chooseFile = (size = 5000) => {
  const input = screen.getByLabelText("Sicherungsdatei") as HTMLInputElement;
  const file = new File([new Uint8Array(size)], "nodvard-deck-sicherung-20261001-030000.ndbak");
  fireEvent.change(input, { target: { files: [file] } });
  return file;
};

beforeEach(() => {
  setUser(true);
  restartTiming.pollMs = 1;
  restartTiming.timeoutMs = 300;
  vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Karte Wiederherstellen: Ablauf", () => {
  it("Datei wählen, hochladen mit Fortschritt, prüfen, Zusammenfassung, einspielen, warten, zur Anmeldung", async () => {
    const calls = mockApi({
      "GET /system/restore/status": () => status(),
      [`POST /system/restore/${ID}/inspect`]: () => staged({ state: "ready", summary: SUMMARY }),
      [`POST /system/restore/${ID}/schedule`]: () => ({ id: ID, source: "owner", scheduled_at: "x", expires_in: 3500, sign_out_all: true, backup: {} }),
      "POST /system/restart": () => ({ restarting: true, exit_code: 75 }),
    });
    let reportProgress: (loaded: number, total: number) => void = () => undefined;
    let finishUpload: () => void = () => undefined;
    const send = vi.spyOn(uploadTransport, "send").mockImplementation((req) => new Promise((resolve) => {
      reportProgress = req.onProgress;
      finishUpload = () => resolve({ status: 201, text: JSON.stringify(staged()) });
    }));
    const health = vi.spyOn(restartProbe, "health");
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 800 }); // vor dem Neustart (Laufzeit merken)
    health.mockResolvedValueOnce(null); // Neustart: nicht erreichbar
    health.mockResolvedValue({ status: "ok", uptime_s: 3 }); // wieder da

    render(<RestoreCard />);
    // Einführung, Datei, Passwort: ohne beides geht Hochladen nicht.
    const upload = await screen.findByRole("button", { name: "Hochladen" });
    expect(screen.getByText(/Wähle eine Sicherungsdatei/)).toBeInTheDocument();
    expect(upload).toBeDisabled();
    const file = chooseFile();
    expect(upload).toBeDisabled();
    typeInto("Anmeldepasswort zum Hochladen", "konto-pw ä%");
    expect(upload).toBeEnabled();
    fireEvent.click(upload);

    // Fortschritt
    const bar = await screen.findByRole("progressbar", { name: "Fortschritt des Hochladens" });
    expect(bar).toHaveAttribute("aria-valuenow", "0");
    reportProgress(2500, 5000);
    await waitFor(() => expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50"));
    expect(screen.getByText(/50 %/)).toBeInTheDocument();
    finishUpload();

    const req = send.mock.calls[0][0];
    expect(req.file).toBe(file);
    expect(req.url).toBe("/api/v1/system/restore/upload");
    expect(req.headers["X-Confirm-Password"]).toBe(encodeURIComponent("konto-pw ä%"));

    // Passwort der Sicherung
    expect(await screen.findByText(/Die Datei ist angekommen/)).toBeInTheDocument();
    expect(screen.getByText(/Einmal-Passwort, das du beim Herunterladen/)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Wiederherstellungsschlüssel" })).not.toBeInTheDocument(); // Einmal-Passwort-Datei: nur Passwort
    typeInto("Passwort der Sicherung", "einmal-passwort-123");
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));

    // Zusammenfassung
    expect(await screen.findByText("Das steckt in der Sicherung")).toBeInTheDocument();
    for (const text of ["anna", "3", "7", "proxmox", "backups", "0.5.0"]) expect(screen.getAllByText(text).length).toBeGreaterThan(0);
    expect(screen.getByText(/0123456789abcdef0123456789abcdef/)).toBeInTheDocument();
    expect(screen.getByText(/von einer anderen Installation/)).toBeInTheDocument();
    expect(screen.getByText("Das Einspielen ersetzt alles – auch die Konten.")).toBeInTheDocument();
    expect(screen.getByText(/prüft nicht,/)).toBeInTheDocument(); // age: keine Absender-Prüfung
    expect(screen.getByText(/dieselben Schlüssel und Zugangsdaten/)).toBeInTheDocument(); // zwei Instanzen
    expect(screen.getByText(/Neustart-Regel/)).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/inspect"))?.body).toEqual({ password: "einmal-passwort-123" });
    expect(screen.queryByDisplayValue("einmal-passwort-123")).not.toBeInTheDocument(); // Feld ist geleert

    const apply = screen.getByRole("button", { name: "Einspielen und neu starten" });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /Ich habe verstanden/ }));
    expect(apply).toBeDisabled(); // Anmeldepasswort fehlt noch
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    expect(apply).toBeEnabled();
    fireEvent.click(apply);

    // Wartebildschirm, danach zur Anmeldung
    expect(await screen.findByTestId("restore-waiting")).toBeInTheDocument();
    await waitFor(() => expect(browserNavigation.assign).toHaveBeenCalledWith("/login"));
    const schedule = calls.find((c) => c.path.endsWith("/schedule"))!;
    expect(schedule.body).toEqual({ current_password: "konto-pw", sign_out_all: true });
    expect(calls.find((c) => c.path === "/system/restart")?.body).toEqual({ current_password: "konto-pw" });
    expect(health.mock.calls.length).toBeGreaterThanOrEqual(3);
    // Nichts Geheimes im Dokument zurückgeblieben.
    expect(document.body.innerHTML).not.toContain("konto-pw");
    expect(document.body.innerHTML).not.toContain("einmal-passwort-123");
  });

  it("falsches Passwort der Sicherung: Meldung, die Datei bleibt, noch ein Versuch", async () => {
    let attempts = 0;
    mockApi({
      "GET /system/restore/status": () => status({ staged: staged() }),
      [`POST /system/restore/${ID}/inspect`]: () => (++attempts === 1
        ? fail(400, "Passwort oder Wiederherstellungsschlüssel passt nicht zu dieser Sicherung.")
        : staged({ state: "ready", summary: SUMMARY })),
    });
    render(<RestoreCard />);
    await screen.findByLabelText("Passwort der Sicherung");
    typeInto("Passwort der Sicherung", "falsch-falsch-1");
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("passt nicht zu dieser Sicherung");
    expect(screen.getByLabelText("Passwort der Sicherung")).toHaveValue("");
    typeInto("Passwort der Sicherung", "richtig-richtig-1");
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    expect(await screen.findByText("Das steckt in der Sicherung")).toBeInTheDocument();
  });

  it("Sicherungspasswort-Datei: Wiederherstellungsschlüssel als zweite Möglichkeit", async () => {
    const calls = mockApi({
      "GET /system/restore/status": () => status({ staged: staged({ header: { mode: "schluessel", created_at: "2026-10-01T03:00:00Z", app_version: "0.5.0" } }) }),
      [`POST /system/restore/${ID}/inspect`]: () => staged({ state: "ready", summary: SUMMARY }),
    });
    render(<RestoreCard />);
    fireEvent.click(await screen.findByRole("radio", { name: "Wiederherstellungsschlüssel" }));
    expect(screen.getByText(/AGE-SECRET-KEY-1/)).toBeInTheDocument();
    typeInto("Wiederherstellungsschlüssel der Sicherung", "AGE-SECRET-KEY-1ABC");
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await screen.findByText("Das steckt in der Sicherung");
    expect(calls.find((c) => c.path.endsWith("/inspect"))?.body).toEqual({ recovery_key: "AGE-SECRET-KEY-1ABC" });
  });

  it("Fehler beim Hochladen (falsches Passwort): Meldung, zurück zum Anfang, Feld leer", async () => {
    mockApi({ "GET /system/restore/status": () => status() });
    vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 400, text: JSON.stringify({ detail: "Das aktuelle Passwort stimmt nicht." }) });
    render(<RestoreCard />);
    await screen.findByRole("button", { name: "Hochladen" });
    chooseFile();
    typeInto("Anmeldepasswort zum Hochladen", "falsch");
    fireEvent.click(screen.getByRole("button", { name: "Hochladen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das aktuelle Passwort stimmt nicht.");
    expect(screen.getByLabelText("Anmeldepasswort zum Hochladen")).toHaveValue("");
  });

  it("Abbrechen während des Hochladens", async () => {
    mockApi({ "GET /system/restore/status": () => status() });
    vi.spyOn(uploadTransport, "send").mockImplementation((req) => new Promise((_resolve, reject) => {
      req.signal?.addEventListener("abort", () => reject(new DOMException("Abgebrochen", "AbortError")));
    }));
    render(<RestoreCard />);
    await screen.findByRole("button", { name: "Hochladen" });
    chooseFile();
    typeInto("Anmeldepasswort zum Hochladen", "pw");
    fireEvent.click(screen.getByRole("button", { name: "Hochladen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Abbrechen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("abgebrochen");
  });

  it("Datei über der Obergrenze: Hinweis, Hochladen gesperrt", async () => {
    mockApi({ "GET /system/restore/status": () => status({ limits: { max_upload_bytes: 1000, max_unpacked_bytes: 5000, expires_in: 3600 } }) });
    render(<RestoreCard />);
    await screen.findByRole("button", { name: "Hochladen" });
    chooseFile(5000);
    typeInto("Anmeldepasswort zum Hochladen", "pw");
    expect(screen.getByRole("button", { name: "Hochladen" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/größer als erlaubt/);
  });

  it("vorgemerkt, aber Neustart gescheitert: nichts geht verloren, noch einmal versuchen oder abbrechen", async () => {
    let restarts = 0;
    const calls = mockApi({
      "GET /system/restore/status": () => status({ staged: staged({ state: "ready", summary: SUMMARY }) }),
      [`POST /system/restore/${ID}/schedule`]: () => ({ id: ID, source: "owner", scheduled_at: "x", expires_in: 3000, sign_out_all: true, backup: { owner_name: "anna" } }),
      "POST /system/restart": () => (++restarts === 1 ? fail(429, "Zu viele Fehlversuche. Bitte in 4 Minuten erneut versuchen.") : { restarting: true, exit_code: 75 }),
      "DELETE /system/restore/pending": () => undefined,
    });
    const health = vi.spyOn(restartProbe, "health");
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 900 }); // Laufzeit vor dem ersten Versuch
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 910 }); // ... und vor dem zweiten
    health.mockResolvedValue({ status: "ok", uptime_s: 1 }); // nach dem Neustart
    render(<RestoreCard />);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Ich habe verstanden/ }));
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    fireEvent.click(screen.getByRole("button", { name: "Einspielen und neu starten" }));
    expect(await screen.findByText(/Der Neustart hat nicht geklappt: Zu viele Fehlversuche/)).toHaveTextContent(/vorgemerkt/);
    expect(screen.getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("");
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    fireEvent.click(screen.getByRole("button", { name: "Jetzt neu starten" }));
    await waitFor(() => expect(browserNavigation.assign).toHaveBeenCalledWith("/login"));
    expect(calls.filter((c) => c.path === "/system/restart")).toHaveLength(2);
  });

  it("falsches Konto-Passwort beim Einspielen: Meldung, Zusammenfassung bleibt, nichts vorgemerkt", async () => {
    mockApi({
      "GET /system/restore/status": () => status({ staged: staged({ state: "ready", summary: SUMMARY }) }),
      [`POST /system/restore/${ID}/schedule`]: () => fail(400, "Das aktuelle Passwort stimmt nicht."),
    });
    render(<RestoreCard />);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Ich habe verstanden/ }));
    typeInto("Anmeldepasswort zur Bestätigung", "falsch");
    fireEvent.click(screen.getByRole("button", { name: "Einspielen und neu starten" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das aktuelle Passwort stimmt nicht.");
    expect(screen.getByText("Das steckt in der Sicherung")).toBeInTheDocument();
  });

  it("Zwei-Faktor an: fragt der Server beim Einspielen nach dem Code, erscheint das Feld und das Passwort bleibt", async () => {
    let answers = 0;
    const calls = mockApi({
      "GET /system/restore/status": () => status({ staged: staged({ state: "ready", summary: SUMMARY }) }),
      [`POST /system/restore/${ID}/schedule`]: () => {
        answers += 1;
        if (answers === 1) {
          return new Response(JSON.stringify({ detail: "Bitte gib zusätzlich den aktuellen Zwei-Faktor-Code aus deiner App ein.", code: "totp_missing" }), { status: 403 });
        }
        if (answers === 2) {
          return new Response(JSON.stringify({ detail: "Der Zwei-Faktor-Code stimmt nicht.", code: "totp_wrong" }), { status: 400 });
        }
        return { id: ID, source: "owner", scheduled_at: "x", expires_in: 3500, sign_out_all: true, backup: {} };
      },
      "POST /system/restart": () => ({ restarting: true, exit_code: 75 }),
    });
    const health = vi.spyOn(restartProbe, "health");
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 800 });
    health.mockResolvedValue({ status: "ok", uptime_s: 3 });
    render(<RestoreCard />);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Ich habe verstanden/ }));
    expect(screen.queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    const apply = screen.getByRole("button", { name: "Einspielen und neu starten" });
    fireEvent.click(apply);
    expect(await screen.findByRole("alert")).toHaveTextContent("Zwei-Faktor-Code");
    expect(screen.getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("konto-pw");
    expect(apply).toBeDisabled();
    typeInto(/^Zwei-Faktor-Code/, "000000");
    fireEvent.click(apply);
    // Vertippt: Das Passwort bleibt stehen, nur der Code muss neu.
    expect(await screen.findByText("Der Zwei-Faktor-Code stimmt nicht.")).toBeInTheDocument();
    expect(screen.getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("konto-pw");
    expect(screen.getByLabelText(/^Zwei-Faktor-Code/)).toHaveValue("");
    expect(apply).toBeDisabled();
    typeInto(/^Zwei-Faktor-Code/, "123456");
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(browserNavigation.assign).toHaveBeenCalledWith("/login"));
    expect(calls.filter((c) => c.path.endsWith("/schedule")).map((c) => c.body)).toEqual([
      { current_password: "konto-pw", sign_out_all: true },
      { current_password: "konto-pw", sign_out_all: true, totp_code: "000000" },
      { current_password: "konto-pw", sign_out_all: true, totp_code: "123456" },
    ]);
    // Der Neustart braucht keinen Code, nur das Passwort.
    expect(calls.find((c) => c.path === "/system/restart")?.body).toEqual({ current_password: "konto-pw" });
    expect(document.body.innerHTML).not.toContain("konto-pw");
  });

  it("Zwei-Faktor an (GET /me): das Code-Feld steht beim Einspielen gleich da, ohne Rückfrage des Servers", async () => {
    const calls = mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", totp_enabled: true }),
      "GET /system/restore/status": () => status({ staged: staged({ state: "ready", summary: SUMMARY }) }),
      [`POST /system/restore/${ID}/schedule`]: () => ({ id: ID, source: "owner", scheduled_at: "x", expires_in: 3500, sign_out_all: true, backup: {} }),
      "POST /system/restart": () => ({ restarting: true, exit_code: 75 }),
    });
    const health = vi.spyOn(restartProbe, "health");
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 800 });
    health.mockResolvedValue({ status: "ok", uptime_s: 3 });
    render(<RestoreCard />);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Ich habe verstanden/ }));
    const field = await screen.findByLabelText(/^Zwei-Faktor-Code/);
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    const apply = screen.getByRole("button", { name: "Einspielen und neu starten" });
    expect(apply).toBeDisabled();
    fireEvent.change(field, { target: { value: "123456" } });
    fireEvent.click(apply);
    await waitFor(() => expect(browserNavigation.assign).toHaveBeenCalledWith("/login"));
    expect(calls.filter((c) => c.path.endsWith("/schedule")).map((c) => c.body)).toEqual([
      { current_password: "konto-pw", sign_out_all: true, totp_code: "123456" },
    ]);
  });

  it("Wartebildschirm: dauert es zu lange, kommt ein Hinweis mit der Neustart-Regel", async () => {
    mockApi({
      "GET /system/restore/status": () => status({ staged: staged({ state: "ready", summary: SUMMARY }) }),
      [`POST /system/restore/${ID}/schedule`]: () => ({ id: ID, source: "owner", scheduled_at: "x", expires_in: 3000, sign_out_all: true, backup: {} }),
      "POST /system/restart": () => ({ restarting: true, exit_code: 75 }),
    });
    vi.spyOn(restartProbe, "health").mockResolvedValue(null); // kommt nie wieder
    restartTiming.timeoutMs = 40;
    render(<RestoreCard />);
    fireEvent.click(await screen.findByRole("checkbox", { name: /Ich habe verstanden/ }));
    typeInto("Anmeldepasswort zur Bestätigung", "konto-pw");
    fireEvent.click(screen.getByRole("button", { name: "Einspielen und neu starten" }));
    expect(await screen.findByText("Das dauert länger als erwartet.")).toBeInTheDocument();
    expect(screen.getByText(/restart: unless-stopped/)).toBeInTheDocument();
    expect(browserNavigation.assign).not.toHaveBeenCalled();
  });
});

describe("Karte Wiederherstellen: Stand vom Server", () => {
  it("Seite neu geladen: geprüfte Sicherung wartet -> Zusammenfassung; hochgeladene -> Passwortfrage; vorgemerkte -> Neustart anbieten", async () => {
    mockApi({ "GET /system/restore/status": () => status({ staged: staged({ state: "ready", summary: SUMMARY }) }) });
    const first = render(<RestoreCard />);
    expect(await screen.findByText("Das steckt in der Sicherung")).toBeInTheDocument();
    first.unmount();

    mockApi({ "GET /system/restore/status": () => status({ staged: staged() }) });
    const second = render(<RestoreCard />);
    expect(await screen.findByLabelText("Passwort der Sicherung")).toBeInTheDocument();
    second.unmount();

    mockApi({
      "GET /system/restore/status": () => status({ pending: { id: ID, source: "owner", scheduled_at: "x", expires_in: 1800, sign_out_all: true, backup: { owner_name: "anna" } } }),
      "DELETE /system/restore/pending": () => undefined,
    });
    render(<RestoreCard />);
    expect(await screen.findByRole("note")).toHaveTextContent(/vorgemerkt.*Owner „anna“/);
    expect(screen.getByRole("button", { name: "Jetzt neu starten" })).toBeDisabled(); // Passwort fehlt
  });

  it("zeigt das letzte Ergebnis (gelungen oder nicht)", async () => {
    mockApi({
      "GET /system/restore/status": () => status({
        result: { ok: false, at: "2026-10-01T03:00:00Z", message: "Die Sicherung ließ sich nicht auf den Stand dieser Version bringen.", source: "owner", actor: "nico", replaced: null, rolled_back: true },
      }),
    });
    render(<RestoreCard />);
    expect(await screen.findByText("nicht eingespielt")).toBeInTheDocument();
    expect(screen.getByText(/ließ sich nicht auf den Stand dieser Version/)).toBeInTheDocument();
  });

  it("alter Stand: Größe zeigen und mit Passwort löschen", async () => {
    let replaced: RestoreStatus["replaced"] = { name: "replaced-20261001T030000", size: 12_000_000, at: "20261001T030000" };
    const calls = mockApi({
      "GET /system/restore/status": () => status({ replaced }),
      "DELETE /system/restore/replaced": () => { replaced = null; return undefined; },
    });
    render(<RestoreCard />);
    expect(await screen.findByText(/replaced-20261001T030000/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Alten Stand löschen/ }));
    const submit = screen.getByRole("button", { name: "Löschen" });
    expect(submit).toBeDisabled();
    typeInto("Anmeldepasswort zum Löschen des alten Stands", "konto-pw");
    fireEvent.click(submit);
    await waitFor(() => expect(screen.queryByText(/replaced-20261001T030000/)).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ current_password: "konto-pw" });
  });

  it("alter Stand: nennt den Tag, an dem er von selbst gelöscht wird", async () => {
    mockApi({
      "GET /system/restore/status": () => status({ replaced: { name: "replaced-20261001T030000", size: 1, at: "20261001T030000", expires_at: "2026-10-31T03:00:00Z" } }),
    });
    render(<RestoreCard />);
    expect(await screen.findByText(/automatisch gelöscht \(30 Tage nach dem Einspielen\)/)).toBeInTheDocument();
    expect(screen.getByText(/Sonst wird er am .*2026.* automatisch gelöscht/)).toBeInTheDocument();
  });

  it("alter Stand ohne Ablaufangabe (älterer Server): kein Satz dazu", async () => {
    mockApi({ "GET /system/restore/status": () => status({ replaced: { name: "replaced-1", size: 1, at: null } }) });
    render(<RestoreCard />);
    await screen.findByText(/replaced-1/);
    expect(screen.queryByText(/automatisch gelöscht/)).not.toBeInTheDocument();
  });

  it("ohne Owner-Recht: nur Hinweis, kein Formular", async () => {
    setUser(false, ["system.read"]);
    mockApi({ "GET /system/restore/status": () => status({ replaced: { name: "replaced-1", size: 1, at: null } }) });
    render(<RestoreCard />);
    expect(await screen.findByText(/nur der Inhaber \(Owner\)/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Sicherungsdatei")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Alten Stand löschen/ })).not.toBeInTheDocument();
  });

  it("Ladefehler wird angezeigt", async () => {
    mockApi({ "GET /system/restore/status": () => fail(409, "Sicherungen gibt es nur, wenn die Datenbank eine SQLite-Datei ist.") });
    render(<RestoreCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("SQLite-Datei");
  });
});

describe("Reiter System", () => {
  it("zeigt die Karte Wiederherstellen neben der Sicherung (mit system.read)", async () => {
    setUser(false, ["system.read"]);
    mockApi({
      "GET /system/backups": () => ({
        config: { enabled: false, schedule: "30 2 * * *", keep: 7, dir: "/app/data/backups", include_runs: false }, key: null,
        target: { dir: "/app/data/backups", default_dir: "/app/data/backups", external_root: "/backups", external_available: false, same_storage_as_data: true, free_bytes: 1, error: null },
        backups: [], last_run: null, running: null, sqlite: true, limits: { keep_min: 1, keep_max: 60, password_min_length: 12 },
      }),
      "GET /system/restore/status": () => status(),
    });
    render(<SystemSettings />);
    expect(await screen.findByText("Wiederherstellen", { selector: "h3" })).toBeInTheDocument();
    expect(await screen.findByText("Sicherung", { selector: "h3" })).toBeInTheDocument();
  });
});

it("ApiError bleibt ein Error mit Meldung (Grundlage der Anzeige)", () => {
  expect(new ApiError(400, "x")).toBeInstanceOf(Error);
});
