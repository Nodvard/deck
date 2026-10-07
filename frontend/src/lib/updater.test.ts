import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { browserNavigation, restartTiming } from "./restore";
import {
  requestRollback,
  requestUpdate,
  resetUpdateHelper,
  tagFitsVersion,
  updaterTiming,
  useUpdateHelper,
  type HelperResult,
  type HelperView,
} from "./updater";

const ID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60";
const NOW = Date.UTC(2026, 9, 3, 10, 0, 0);

const READY: HelperView = {
  present: true, reason: null, ready: true, ready_reason: null, state: "idle", helper_version: "0.7.0", heartbeat_at: NOW / 1000,
  target: { current_version: "0.7.0", floating_tag: "latest", pinned: false }, busy: null, previous: null, last_result: null, pending: null,
};

const busy = (step: string): HelperView => ({
  ...READY, ready: false, ready_reason: "busy", state: "busy", target: null, busy: { id: ID, action: "update", step, since: NOW / 1000 },
});

const result = (outcome: HelperResult["outcome"], code: string | null = null, patch: Partial<HelperResult> = {}): HelperResult => ({
  id: ID, action: "update", from: "0.7.0", to: "0.7.1", outcome, code, finished_at: NOW / 1000 + 120, ...patch,
});

type HelperAnswer = HelperView | "down";
type HealthAnswer = { status: string; version?: string; uptime_s?: number } | null;

/** Antworten der Reihe nach; die letzte bleibt stehen. */
function stub(helper: HelperAnswer[], health: HealthAnswer[] = []) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const next = <T,>(list: T[], fallback: T): T => (list.length > 1 ? list.shift()! : list[0] ?? fallback);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    calls.push({ method, path, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    if (path === "/health") {
      const answer = next(health, null);
      if (!answer) throw new TypeError("offline");
      return new Response(JSON.stringify(answer), { status: answer.status === "ok" ? 200 : 503 });
    }
    if (path === "/system/updates/helper") {
      const answer = next(helper, READY);
      if (answer === "down") throw new TypeError("offline");
      return new Response(JSON.stringify(answer), { status: 200 });
    }
    if (method === "POST") return new Response(JSON.stringify({ request_id: ID, action: "update", from: "0.7.0", to: "0.7.1", data_revert: false }), { status: 202 });
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  }));
  return calls;
}

const follow = () => useUpdateHelper.getState().follow!;

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
  useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: null });
  resetUpdateHelper();
  restartTiming.pollMs = 2000;
  updaterTiming.pollMs = 3000;
  updaterTiming.timeoutMs = 30 * 60 * 1000;
  updaterTiming.reloadDelayMs = 3000;
});
afterEach(() => {
  resetUpdateHelper();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Aufträge an den Update-Helfer", () => {
  it("schickt Version und Passwort, den Code nur, wenn es einen gibt", async () => {
    vi.useRealTimers();
    const calls = stub([READY]);
    await requestUpdate("0.7.1", "geheim", "");
    await requestUpdate("0.7.1", "geheim", " 123456 ");
    await requestRollback("geheim", true, "");
    await requestRollback("geheim", false, "654321");
    expect(calls.map((c) => [c.path, c.body])).toEqual([
      ["/system/updates/apply", { version: "0.7.1", current_password: "geheim" }],
      ["/system/updates/apply", { version: "0.7.1", current_password: "geheim", totp_code: "123456" }],
      ["/system/updates/rollback", { current_password: "geheim", accept_data_loss: true }],
      ["/system/updates/rollback", { current_password: "geheim", accept_data_loss: false, totp_code: "654321" }],
    ]);
  });

  it("das Tag der Compose-Datei passt wie im Helfer", () => {
    expect(tagFitsVersion("latest", "0.8.0")).toBe(true);
    expect(tagFitsVersion("0.7", "0.7.3")).toBe(true);
    expect(tagFitsVersion("0.7", "0.8.0")).toBe(false);
    expect(tagFitsVersion("0.7", "10.7.0")).toBe(false);
    expect(tagFitsVersion(null, "0.7.0")).toBe(false);
  });
});

describe("Einem Vorgang folgen", () => {
  it("Phase 1 zeigt die Schritte, Phase 2 wartet auf /health, danach das Ergebnis und Neuladen", async () => {
    const assign = vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
    const helper: HelperAnswer[] = [{ ...READY, pending: { id: ID, action: "update", to: "0.7.1", at: NOW / 1000 } }];
    const calls = stub(helper, [null, null, { status: "ok", version: "0.7.1", uptime_s: 3 }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", from: "0.7.0", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(0);
    expect(follow().phase).toBe("waiting");

    helper.splice(0, helper.length, busy("begin"));
    await vi.advanceTimersByTimeAsync(3000);
    expect([follow().phase, follow().step]).toEqual(["working", "begin"]);

    helper.splice(0, helper.length, busy("old_stopped"), "down");
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().step).toBe("old_stopped");
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().phase).toBe("restarting");
    expect(calls.filter((c) => c.path === "/health")).toHaveLength(0);

    // Zwei Abfragen ohne Antwort, dann antwortet die neue Version gesund; zurück in Phase 1 wartet der Helfer noch auf
    // den Gesundheitscheck von Docker (der Schritt bleibt „started“), danach steht das Ergebnis da. Dazwischen bleibt es
    // bei „antwortet, wird geprüft“, nicht wieder „startet“.
    helper.splice(0, helper.length, busy("started"), { ...READY, last_result: result("applied") });
    await vi.advanceTimersByTimeAsync(2000 * 3);
    expect(calls.filter((c) => c.path === "/health")).toHaveLength(3);
    expect([follow().phase, follow().step, follow().running]).toEqual(["checking", "started", "0.7.1"]);
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().phase).toBe("done");
    expect(follow().result?.outcome).toBe("applied");
    expect(follow().reloading).toBe(true);
    expect(assign).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(3000);
    expect(assign).toHaveBeenCalledWith("/settings/system");
  });

  it("nach einem Rückbau (rolled_back) bleibt die Seite und zeigt das Ergebnis", async () => {
    const assign = vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
    stub([busy("started"), "down", { ...READY, last_result: result("rolled_back", "exited") }], [{ status: "ok", version: "0.7.0", uptime_s: 2 }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", from: "0.7.0", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().phase).toBe("restarting");
    await vi.advanceTimersByTimeAsync(2000);
    expect([follow().phase, follow().running]).toEqual(["done", "0.7.0"]);
    expect(follow().result).toMatchObject({ outcome: "rolled_back", code: "exited" });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(assign).not.toHaveBeenCalled();
    expect(follow().reloading).toBe(false);
  });

  it("Rückbau nach dem Umschalten: antwortet wieder die alte Version, bleibt die Anzeige dabei, solange der Helfer zurückbaut", async () => {
    // Der Rückbau ändert `busy.step` nicht (er bleibt „started“), bis das Ergebnis da ist.
    const helper: HelperAnswer[] = [busy("started"), "down"];
    stub(helper, [{ status: "ok", version: "0.7.0", uptime_s: 2 }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", from: "0.7.0", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(0);
    expect([follow().phase, follow().step]).toEqual(["working", "started"]);
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().phase).toBe("restarting");
    helper.splice(0, helper.length, busy("started"));
    await vi.advanceTimersByTimeAsync(2000);
    expect([follow().phase, follow().step, follow().running]).toEqual(["checking", "started", "0.7.0"]);
    await vi.advanceTimersByTimeAsync(3000 * 10);
    expect([follow().phase, follow().running]).toEqual(["checking", "0.7.0"]);
    helper.splice(0, helper.length, { ...READY, last_result: result("rolled_back", "exited") });
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().result?.outcome).toBe("rolled_back");
  });

  it("war das Dashboard vor dem Umschalten nur kurz weg, zeigt die Anzeige danach wieder den Schritt des Helfers", async () => {
    const helper: HelperAnswer[] = [busy("begin"), "down"];
    stub(helper, [{ status: "ok", version: "0.7.0", uptime_s: 900 }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", from: "0.7.0", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(3000);
    expect(follow().phase).toBe("restarting");
    helper.splice(0, helper.length, busy("pulled"));
    await vi.advanceTimersByTimeAsync(2000);
    expect([follow().phase, follow().step, follow().running]).toEqual(["working", "pulled", "0.7.0"]);
  });

  it("ein fremdes Ergebnis zählt nicht, nur das zur eigenen Anforderung", async () => {
    const other = { ...result("applied"), id: "0b7e3a51-2c4d-4e6f-9a10-2b3c4d5e6f70" };
    stub([{ ...READY, last_result: other }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(follow().phase).toBe("waiting");
  });

  it("antwortet der Helfer nicht, wartet der Auftrag und die Anzeige sagt es", async () => {
    stub([{ ...READY, present: false, reason: "stale", ready: false, target: null, pending: { id: ID, action: "update", to: "0.7.1", at: NOW / 1000 } }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(0);
    expect([follow().phase, follow().helperAbsent]).toEqual(["waiting", true]);
  });

  it("nach 30 Minuten ohne Ergebnis: Zeitüberschreitung", async () => {
    stub([{ ...READY, pending: { id: ID, action: "update", to: "0.7.1", at: NOW / 1000 } }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(29 * 60 * 1000);
    expect(follow().phase).toBe("waiting");
    await vi.advanceTimersByTimeAsync(2 * 60 * 1000);
    expect(follow().phase).toBe("timeout");
    expect(follow().watching).toBe(true);
    expect(follow().elapsedS).toBeGreaterThanOrEqual(30 * 60);
  });

  it("nach der Frist fragt die Anzeige seltener weiter nach und zeigt ein spätes Ergebnis (langsamer Download, Rückbau)", async () => {
    const helper: HelperAnswer[] = [busy("begin")];
    const calls = stub(helper);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", from: "0.7.0", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(30 * 60 * 1000 + 5000);
    expect([follow().phase, follow().watching]).toEqual(["timeout", true]);
    const before = calls.length;
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    // alle 30 Sekunden statt alle 3
    expect(calls.length - before).toBe(20);
    expect(follow().phase).toBe("timeout");
    helper.splice(0, helper.length, { ...READY, last_result: result("rolled_back", "timeout") });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(follow().phase).toBe("done");
    expect(follow().result).toMatchObject({ outcome: "rolled_back", code: "timeout" });
  });

  it("nach der Frist: arbeitet der Helfer nicht mehr an dem Auftrag und gibt es kein Ergebnis, hört die Anzeige auf", async () => {
    const helper: HelperAnswer[] = [busy("begin")];
    const calls = stub(helper);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(30 * 60 * 1000 + 5000);
    expect(follow().watching).toBe(true);
    helper.splice(0, helper.length, READY);
    await vi.advanceTimersByTimeAsync(30_000);
    expect([follow().phase, follow().watching]).toEqual(["timeout", false]);
    const before = calls.length;
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    expect(calls.length).toBe(before);
  });

  it("„Schließen“ nach der Frist, während der Helfer noch arbeitet: wieder der Fortschritt, nicht gleich die nächste Frist", async () => {
    stub([{ ...busy("begin"), busy: { id: ID, action: "update", step: "begin", since: NOW / 1000 } }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1", startedAt: NOW });
    await vi.advanceTimersByTimeAsync(30 * 60 * 1000 + 5000);
    expect(follow().phase).toBe("timeout");
    useUpdateHelper.getState().dismiss();
    await vi.advanceTimersByTimeAsync(100);
    expect([follow().phase, follow().step]).toEqual(["working", "begin"]);
    // Die Dauer zählt weiter ab dem Beginn des Vorgangs.
    expect(follow().startedAt).toBe(NOW);
    await vi.advanceTimersByTimeAsync(29 * 60 * 1000);
    expect(follow().phase).toBe("working");
  });

  it("bleibt das Dashboard bis zur Frist weg, gilt sie auch in Phase 2", async () => {
    stub(["down"], [null]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(0);
    expect(follow().phase).toBe("restarting");
    await vi.advanceTimersByTimeAsync(31 * 60 * 1000);
    expect(follow().phase).toBe("timeout");
  });

  it("nach der Frist zählt das Ergebnis aus last_result", async () => {
    const helper: HelperAnswer[] = [{ ...READY, pending: { id: ID, action: "update", to: "0.7.1", at: NOW / 1000 } }];
    stub(helper);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1", startedAt: NOW });
    await vi.advanceTimersByTimeAsync(30 * 60 * 1000 - 1000);
    helper.splice(0, helper.length, { ...READY, last_result: result("failed_manual", "rollback_failed") });
    await vi.advanceTimersByTimeAsync(5000);
    expect(follow().phase).toBe("done");
    expect(follow().result?.outcome).toBe("failed_manual");
  });

  it("beim Laden: ein laufender Vorgang wird weiter verfolgt (Seite neu geladen)", async () => {
    stub([{ ...busy("pulled"), busy: { id: ID, action: "rollback", step: "pulled", since: NOW / 1000 - 60 } }]);
    await useUpdateHelper.getState().load();
    await vi.advanceTimersByTimeAsync(0);
    expect(follow()).toMatchObject({ requestId: ID, action: "rollback", phase: "working", step: "pulled", startedAt: NOW - 60_000 });
  });

  it("abgemeldet (die zurückgeholte Datenbank kennt die Anmeldung nicht): kein halber Fortschritt bleibt stehen", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^\/api\/v1/, "");
      if (path === "/auth/refresh") return new Response(JSON.stringify({ detail: "abgelaufen" }), { status: 401 });
      return new Response(JSON.stringify({ detail: "Nicht angemeldet." }), { status: 401 });
    }));
    useUpdateHelper.setState({ loaded: true });
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "rollback", to: "0.7.0", dataRevert: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useUpdateHelper.getState().follow).toBeNull();
    expect(useUpdateHelper.getState().loaded).toBe(false);
  });

  it("Schließen beendet das Folgen und liest den Zustand neu", async () => {
    stub([{ ...READY, last_result: result("refused", "rate_limited") }]);
    useUpdateHelper.getState().startFollow({ requestId: ID, action: "update", to: "0.7.1" });
    await vi.advanceTimersByTimeAsync(0);
    expect(follow().phase).toBe("done");
    useUpdateHelper.getState().dismiss();
    expect(useUpdateHelper.getState().follow).toBeNull();
    await vi.advanceTimersByTimeAsync(0);
    expect(useUpdateHelper.getState().view?.last_result?.outcome).toBe("refused");
  });
});
