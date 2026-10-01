import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { describeActionOutcome, RUNNING_IN_BACKGROUND, runAction, waitForAction } from "./actions";

type Reply = { status: number; body?: unknown } | Error;

let permissions: Set<string>;
let calls: { url: string; method: string }[];

/** Antwortet je URL der Reihe nach; die letzte Antwort bleibt stehen. */
function stubFetch(routes: Record<string, Reply[]>) {
  const counters: Record<string, number> = {};
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    const url = String(input).replace("/api/v1", "");
    calls.push({ url, method: init.method ?? "GET" });
    const list = routes[url];
    if (!list) return new Response(JSON.stringify({ detail: "unbekannt" }), { status: 404 });
    const i = Math.min(counters[url] ?? 0, list.length - 1);
    counters[url] = (counters[url] ?? 0) + 1;
    const reply = list[i];
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body ?? {}), { status: reply.status });
  }));
}

beforeEach(() => {
  vi.useFakeTimers();
  permissions = new Set(["actions.approve:medium"]);
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission: (p: string) => permissions.has(p),
  };
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("describeActionOutcome", () => {
  it("spiegelt die Texte des Kerns", () => {
    expect(describeActionOutcome({ status: "succeeded" })).toEqual({ tone: "success", text: "Ausgeführt" });
    expect(describeActionOutcome({ status: "failed", result: { error: "VM is locked" } })).toEqual({ tone: "error", text: "Fehlgeschlagen: VM is locked" });
    expect(describeActionOutcome({ status: "failed" }).text).toBe("Fehlgeschlagen: unbekannter Fehler");
    expect(describeActionOutcome({ status: "denied", gate_decision: { rule: "blocklist", detail: "Server gesperrt" } }).text).toBe("Gesperrt: Server gesperrt");
    expect(describeActionOutcome({ status: "denied", gate_decision: { rule: "user:reject", user_reason: "falsch" } }).text).toBe("Abgelehnt: falsch");
    expect(describeActionOutcome({ status: "executing" })).toEqual({ tone: "pending", text: "Läuft noch …" });
    expect(describeActionOutcome({ status: "proposed" }).text).toBe("Wartet auf Freigabe");
    expect(describeActionOutcome({ status: "komisch" }).text).toBe("Unbekannter Zustand (komisch)");
  });
});

describe("waitForAction", () => {
  it("fragt alle 3 s nach, bis die Aktion fertig ist", async () => {
    stubFetch({ "/actions/a1": [{ status: 200, body: { status: "executing" } }, { status: 200, body: { status: "executing" } }, { status: 200, body: { status: "succeeded", result: { output: "ok" } } }] });
    const done = waitForAction("a1");
    await vi.advanceTimersByTimeAsync(2999);
    expect(calls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(6000);
    await expect(done).resolves.toMatchObject({ status: "succeeded", result: { output: "ok" } });
    expect(calls).toHaveLength(3);
  });

  it("ueberspringt kurze Aussetzer (5xx, Netzwerkfehler), wirft aber bei 4xx", async () => {
    stubFetch({ "/actions/a1": [{ status: 502 }, new TypeError("Failed to fetch"), { status: 200, body: { status: "failed", result: { error: "kaputt" } } }] });
    const done = waitForAction("a1");
    await vi.advanceTimersByTimeAsync(9000);
    await expect(done).resolves.toMatchObject({ status: "failed" });

    stubFetch({ "/actions/a2": [{ status: 404, body: { detail: "Unbekannte Aktion." } }] });
    const caught = waitForAction("a2").catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(3000);
    expect(String(await caught)).toContain("Unbekannte Aktion.");
  });

  it("401, und der Server antwortet beim Erneuern nicht (Neustart): fragt weiter nach", async () => {
    window.__lattice.refreshAccessTokenResult = vi.fn(async (): Promise<NodvardDeckTokenRefreshResult> => ({ status: "unavailable" }));
    stubFetch({ "/actions/a1": [{ status: 401, body: { detail: "Nicht authentifiziert." } }, { status: 200, body: { status: "succeeded" } }] });
    const done = waitForAction("a1");
    await vi.advanceTimersByTimeAsync(6000);
    await expect(done).resolves.toMatchObject({ status: "succeeded" });
    expect(window.__lattice.refreshAccessTokenResult).toHaveBeenCalledTimes(1);
  });

  it("401, und der Server lehnt die Anmeldung ab: bricht mit „Nicht authentifiziert.“ ab", async () => {
    window.__lattice.refreshAccessTokenResult = vi.fn(async (): Promise<NodvardDeckTokenRefreshResult> => ({ status: "rejected" }));
    stubFetch({ "/actions/a1": [{ status: 401, body: { detail: "Nicht authentifiziert." } }, { status: 200, body: { status: "succeeded" } }] });
    const caught = waitForAction("a1").catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(3000);
    expect(String(await caught)).toContain("Nicht authentifiziert.");
    expect(calls).toHaveLength(1);
  });

  it("gibt nach der Hoechstzeit den letzten Stand (noch laufend) zurueck", async () => {
    stubFetch({ "/actions/a1": [{ status: 200, body: { status: "executing" } }] });
    const done = waitForAction("a1", { id: "a1" }, { maxMs: 10_000 });
    await vi.advanceTimersByTimeAsync(12_000);
    await expect(done).resolves.toMatchObject({ status: "executing" });
  });

  it("bricht mit dem Signal ab", async () => {
    stubFetch({ "/actions/a1": [{ status: 200, body: { status: "executing" } }] });
    const controller = new AbortController();
    const caught = waitForAction("a1", {}, { signal: controller.signal }).catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(3000);
    controller.abort();
    await vi.advanceTimersByTimeAsync(3000);
    expect(((await caught) as DOMException).name).toBe("AbortError");
    const before = calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls.length).toBe(before);
  });

  it("fragt im Hintergrund-Tab nicht nach und macht danach weiter", async () => {
    stubFetch({ "/actions/a1": [{ status: 200, body: { status: "succeeded" } }] });
    let hidden = true;
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
    const done = waitForAction("a1", {}, { maxMs: 60_000 });
    await vi.advanceTimersByTimeAsync(12_000);
    expect(calls).toHaveLength(0);
    hidden = false;
    await vi.advanceTimersByTimeAsync(3000);
    await expect(done).resolves.toMatchObject({ status: "succeeded" });
    expect(calls).toHaveLength(1);
    vi.restoreAllMocks();
  });
});

describe("runAction", () => {
  it("schlaegt vor, gibt bei Recht frei und zeigt das Ergebnis", async () => {
    stubFetch({
      "/ext/x/run": [{ status: 200, body: { action_id: "a1", status: "proposed", risk: "medium" } }],
      "/actions/a1/approve": [{ status: 200, body: { id: "a1", status: "succeeded", result: { output: "fertig" } } }],
    });
    const run = await runAction("/ext/x/run", { method: "POST", body: "{}" });
    expect(run).toMatchObject({ tone: "success", approved: true, action: { status: "succeeded", result: { output: "fertig" } } });
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(["POST /ext/x/run", "POST /actions/a1/approve"]);
  });

  it("laesst den Vorschlag offen, wenn die Freigabe-Berechtigung fehlt", async () => {
    permissions.clear();
    stubFetch({ "/ext/x/run": [{ status: 200, body: { action_id: "a1", status: "proposed", risk: "medium" } }] });
    const run = await runAction("/ext/x/run", { method: "POST" });
    expect(run).toMatchObject({ tone: "pending", text: "Wartet auf Freigabe", approved: false });
    expect(calls).toHaveLength(1);
  });

  it("fragt bei 202 'executing' alle 3 s nach, bis das Ergebnis da ist", async () => {
    stubFetch({
      "/ext/x/run": [{ status: 200, body: { id: "a1", status: "proposed", risk: "medium" } }],
      "/actions/a1/approve": [{ status: 202, body: { id: "a1", status: "executing" } }],
      "/actions/a1": [{ status: 200, body: { id: "a1", status: "executing" } }, { status: 200, body: { id: "a1", status: "failed", result: { error: "Zeitüberschreitung" } } }],
    });
    const pending = runAction("/ext/x/run", { method: "POST" });
    await vi.advanceTimersByTimeAsync(6000);
    await expect(pending).resolves.toMatchObject({ tone: "error", text: "Fehlgeschlagen: Zeitüberschreitung", approved: true });
    expect(calls.filter((c) => c.url === "/actions/a1")).toHaveLength(2);
  });

  it("fragt auch nach, wenn schon der Vorschlag laeuft (Auto-Freigabe)", async () => {
    stubFetch({
      "/ext/x/run": [{ status: 200, body: { action_id: "a1", status: "executing" } }],
      "/actions/a1": [{ status: 200, body: { id: "a1", status: "succeeded" } }],
    });
    const pending = runAction("/ext/x/run", { method: "POST" });
    await vi.advanceTimersByTimeAsync(3000);
    await expect(pending).resolves.toMatchObject({ tone: "success", approved: false });
  });

  it("nimmt die Risikostufe aus den Optionen, wenn die Route keine nennt", async () => {
    stubFetch({
      "/ext/x/run": [{ status: 200, body: { action_id: "a1", status: "proposed" } }],
      "/actions/a1/approve": [{ status: 200, body: { status: "succeeded" } }],
    });
    const run = await runAction("/ext/x/run", { method: "POST" }, { risk: "medium" });
    expect(run.approved).toBe(true);
  });

  it("meldet beim Abbruch (Seite verlassen) 'Läuft im Hintergrund' statt eines Fehlers", async () => {
    stubFetch({
      "/ext/x/run": [{ status: 200, body: { id: "a1", status: "executing" } }],
      "/actions/a1": [{ status: 200, body: { id: "a1", status: "executing" } }],
    });
    const controller = new AbortController();
    const pending = runAction("/ext/x/run", { method: "POST" }, { signal: controller.signal });
    await vi.advanceTimersByTimeAsync(3000);
    controller.abort();
    await expect(pending).resolves.toMatchObject({ tone: "pending", text: RUNNING_IN_BACKGROUND, action: { status: "executing" } });
    const before = calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(calls.length).toBe(before);
  });

  it("wirft mit dem Grund, wenn die Anfrage oder die Freigabe scheitert", async () => {
    stubFetch({ "/ext/x/run": [{ status: 409, body: { detail: "Läuft schon." } }] });
    await expect(runAction("/ext/x/run", { method: "POST" })).rejects.toThrow("Läuft schon.");

    stubFetch({
      "/ext/x/run": [{ status: 200, body: { action_id: "a1", status: "proposed", risk: "medium" } }],
      "/actions/a1/approve": [{ status: 409, body: { detail: "Vorschlag ist nicht mehr im Zustand 'proposed'." } }],
    });
    await expect(runAction("/ext/x/run", { method: "POST" })).rejects.toThrow("nicht mehr im Zustand");
  });
});
