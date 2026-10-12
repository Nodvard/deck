import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScriptsPage } from "./ScriptsPage";

/** Seiten-Durchgang: Ladefehler sind keine leeren Listen ("Noch keine Läufe", "Keine Versionen"). */

const base = { description: "", content: "uptime\n", params_schema: {}, target: { kind: "host", host_id: "h-pi" }, schedule: "0 3 * * *", enabled: true };
const UPTIME = { ...base, id: "uptime", name: "Uptime", job_id: "script-uptime" };
const DISK = { ...base, id: "disk", name: "Platte", job_id: "script-disk" };
const JOBS = [{ id: "j1", ext_job_key: "script-uptime", next_run_at: null, enabled: true }];
const RUN = { id: "r1", status: "succeeded", started_at: "2026-10-01T03:00:00Z", trigger: "schedule", error: null };

type Handler = (method: string) => Response | Promise<Response>;
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
const fail = (detail: string) => new Response(JSON.stringify({ detail }), { status: 500 });

function mockFetch(routes: Record<string, Handler>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const path = url.replace(/^.*\/api\/v1/, "");
    const handler = routes[path];
    if (handler) return handler(method);
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  });
}

function defaults(): Record<string, Handler> {
  return {
    "/ext/scripts/scripts": () => ok([UPTIME, DISK]),
    "/jobs?ext_id=scripts": () => ok(JOBS),
    "/jobs/j1/runs?limit=10": () => ok([RUN]),
    "/ext/scripts/scripts/uptime/runs": () => ok([]),
    "/ext/scripts/scripts/uptime/history": () => ok([{ sha: "abc1234", message: "Uptime angelegt", commit_time: 1759000000 }]),
    "/ext/scripts/scripts/disk/runs": () => ok([]),
    "/ext/scripts/scripts/disk/history": () => ok([]),
    "/hosts": () => ok([{ id: "h-pi", name: "pi", display_name: "Raspberry Pi" }]),
    "/host-groups": () => ok([]),
  };
}

beforeEach(() => {
  window.history.replaceState({}, "", "/ext/scripts/scripts");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("ScriptsPage bei Ladefehlern", () => {
  it("Skriptliste: Grund statt „Noch keine Skripte“, erneut versuchen", async () => {
    let broken = true;
    vi.stubGlobal("fetch", mockFetch({ ...defaults(), "/ext/scripts/scripts": () => (broken ? fail("Datenbank gerade gesperrt.") : ok([UPTIME])) }));
    render(<ScriptsPage />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Liste der Skripte ließ sich gerade nicht laden: Datenbank gerade gesperrt.");
    expect(screen.queryByText("Noch keine Skripte")).toBeNull();
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Uptime")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });

  it("Läufe, Ausführungen und Versionen: Grund statt „Noch keine …“", async () => {
    vi.stubGlobal("fetch", mockFetch({
      ...defaults(),
      "/jobs/j1/runs?limit=10": () => fail("Zeitplan-Speicher nicht lesbar."),
      "/ext/scripts/scripts/uptime/runs": () => fail("Aktionen nicht lesbar."),
      "/ext/scripts/scripts/uptime/history": () => fail("Git-Ablage kaputt."),
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    await waitFor(() => expect(screen.getAllByTestId("load-error")).toHaveLength(3));
    const texts = screen.getAllByTestId("load-error").map((e) => e.textContent);
    expect(texts).toContain("Die Liste der Ausführungen ließ sich gerade nicht laden: Aktionen nicht lesbar.Erneut versuchen");
    expect(texts.some((t) => t?.startsWith("Die Liste der Zeitplan-Läufe ließ sich gerade nicht laden: Zeitplan-Speicher nicht lesbar."))).toBe(true);
    expect(texts.some((t) => t?.startsWith("Die Liste der Versionen ließ sich gerade nicht laden: Git-Ablage kaputt."))).toBe(true);
    expect(screen.queryByText("Noch keine Läufe.")).toBeNull();
    expect(screen.queryByText("Keine Versionen.")).toBeNull();
    expect(screen.queryByText("Noch nicht ausgeführt.")).toBeNull();
  });

  it("Zeitpläne nicht ladbar: die Läufe sagen es, erneut versuchen holt Zeitpläne und Läufe", async () => {
    let jobsBroken = true;
    vi.stubGlobal("fetch", mockFetch({ ...defaults(), "/jobs?ext_id=scripts": () => (jobsBroken ? fail("Planer nicht bereit.") : ok(JOBS)) }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    const alert = await screen.findByTestId("load-error");
    expect(alert).toHaveTextContent("Die Liste der Zeitplan-Läufe ließ sich gerade nicht laden: Planer nicht bereit.");
    jobsBroken = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("erfolgreich")).toBeInTheDocument();
    expect(screen.queryByTestId("load-error")).toBeNull();
  });

  it("eine späte Antwort zum vorher gewählten Skript überschreibt nicht das jetzt gewählte", async () => {
    let releaseHistory: (r: Response) => void = () => undefined;
    vi.stubGlobal("fetch", mockFetch({
      ...defaults(),
      "/ext/scripts/scripts/uptime/history": () => new Promise<Response>((resolve) => { releaseHistory = resolve; }),
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(screen.getByText("Platte"));
    expect(await screen.findByText("Keine Versionen.")).toBeInTheDocument();
    releaseHistory(ok([{ sha: "abc1234", message: "Uptime angelegt", commit_time: 1759000000 }]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Uptime angelegt")).toBeNull();
    expect(screen.getByText("Keine Versionen.")).toBeInTheDocument();
  });

  it("solange die Listen laden, steht „Lade …“ statt „Noch keine …“", async () => {
    const pending = () => new Promise<Response>(() => undefined);
    vi.stubGlobal("fetch", mockFetch({
      ...defaults(),
      "/jobs/j1/runs?limit=10": pending,
      "/ext/scripts/scripts/uptime/runs": pending,
      "/ext/scripts/scripts/uptime/history": pending,
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    await waitFor(() => expect(screen.getAllByText("Lade …").length).toBeGreaterThanOrEqual(3));
    expect(screen.queryByText("Noch keine Läufe.")).toBeNull();
    expect(screen.queryByText("Keine Versionen.")).toBeNull();
    expect(screen.queryByText("Noch nicht ausgeführt.")).toBeNull();
  });

  it("scheitert beim Neuladen die Skriptliste, bleibt der Fehler der Zeitpläne bestehen", async () => {
    let scriptCalls = 0;
    let jobCalls = 0;
    vi.stubGlobal("fetch", mockFetch({
      ...defaults(),
      "/ext/scripts/scripts": () => (++scriptCalls === 1 ? ok([UPTIME, DISK]) : fail("Datenbank gerade gesperrt.")),
      // Erstes Laden und der Nachversuch beim Klick scheitern, danach geht es.
      "/jobs?ext_id=scripts": () => (++jobCalls <= 2 ? fail("Planer nicht bereit.") : ok(JOBS)),
      "/ext/scripts/scripts/uptime": (method) => (method === "PUT" ? ok(UPTIME) : fail("unerwartet")),
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    expect(await screen.findByText(/Die Liste der Zeitplan-Läufe ließ sich gerade nicht laden: Planer nicht bereit/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText(/Die Liste der Skripte ließ sich gerade nicht laden/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Platte"));
    fireEvent.click(screen.getByText("Uptime"));
    expect(await screen.findByText("erfolgreich")).toBeInTheDocument();
  });

  it("wer während des Speicherns ein anderes Skript wählt, bleibt dort", async () => {
    let releasePut: (r: Response) => void = () => undefined;
    vi.stubGlobal("fetch", mockFetch({
      ...defaults(),
      "/ext/scripts/scripts/uptime": (method) => (method === "PUT"
        ? new Promise<Response>((resolve) => { releasePut = resolve; })
        : fail("unerwartet")),
    }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    fireEvent.click(screen.getByText("Platte"));
    expect(screen.getByLabelText("Name")).toHaveValue("Platte");
    releasePut(ok({ ...UPTIME, fingerprint: "neu" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByLabelText("Name")).toHaveValue("Platte");
    expect(screen.queryByText("Gespeichert.")).toBeNull();
  });

  it("Server und Gruppen nicht ladbar: Hinweis am Ziel", async () => {
    vi.stubGlobal("fetch", mockFetch({ ...defaults(), "/hosts": () => fail("Serverliste nicht lesbar.") }));
    render(<ScriptsPage />);
    fireEvent.click(await screen.findByText("Uptime"));
    expect(await screen.findByTestId("load-error")).toHaveTextContent(
      "Die Liste der Server und Gruppen ließ sich gerade nicht laden: Serverliste nicht lesbar.",
    );
  });
});
