import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import {
  bootstrapAfterRestart,
  cancelRestore,
  confirmHeader,
  errorMessage,
  formatDuration,
  inspectBackup,
  readUptime,
  restartProbe,
  restartServer,
  restartTiming,
  scheduleRestore,
  uploadBackup,
  uploadTransport,
  waitForRestart,
} from "./restore";

const file = new File([new Uint8Array(10)], "sicherung.ndbak");
const STAGED = { id: "a".repeat(32), state: "uploaded", size: 10, header: { mode: "passwort", created_at: "2026-10-01T03:00:00Z", app_version: "0.5.0" }, summary: null, expires_in: 3600 };

type Call = { method: string; path: string; headers: Record<string, string>; body: unknown };

function stubFetch(handler: (call: Call) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((value, name) => { headers[name.toLowerCase()] = value; });
    const call = { method: init?.method ?? "GET", path: String(input).replace(/^\/api\/v1/, ""), headers, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined };
    calls.push(call);
    return handler(call);
  }));
  return calls;
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null, user: null });
  restartTiming.pollMs = 1;
  restartTiming.timeoutMs = 200;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Kopf mit dem Passwort", () => {
  it("kodiert Unicode und Sonderzeichen prozentweise (UTF-8)", () => {
    expect(confirmHeader("abc")).toBe("abc");
    expect(confirmHeader("Grüße 100% 🙂")).toBe("Gr%C3%BC%C3%9Fe%20100%25%20%F0%9F%99%82");
    expect(decodeURIComponent(confirmHeader("Grüße 100% 🙂"))).toBe("Grüße 100% 🙂");
  });
});

describe("Fehlertexte", () => {
  it("nimmt detail als Satz oder Liste, sonst HTTP-Status oder die Standardmeldung", () => {
    expect(errorMessage(400, JSON.stringify({ detail: "Passwort falsch." }))).toBe("Passwort falsch.");
    expect(errorMessage(422, JSON.stringify({ detail: [{ msg: "a" }, { msg: "b" }] }))).toBe("a; b");
    expect(errorMessage(500, "<html>")).toBe("HTTP 500");
    expect(errorMessage(503, "")).toMatch(/nicht erreichbar/);
  });

  it("formatiert Zeiten", () => {
    expect(formatDuration(12)).toBe("12 Sek.");
    expect(formatDuration(75)).toBe("1 Min.");
    expect(formatDuration(600)).toBe("10 Min.");
  });
});

describe("Hochladen", () => {
  it("Owner: roher Strom mit Passwort (prozentkodiert) im Kopf und Bearer-Token", async () => {
    const send = vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 201, text: JSON.stringify(STAGED) });
    const onProgress = vi.fn();
    const out = await uploadBackup(file, { mode: "owner", accountPassword: "pä ss%" }, { onProgress });
    expect(out.id).toBe(STAGED.id);
    const req = send.mock.calls[0][0];
    expect(req.url).toBe("/api/v1/system/restore/upload");
    expect(req.file).toBe(file);
    expect(req.headers).toEqual({ "X-Confirm-Password": "p%C3%A4%20ss%25", Authorization: "Bearer tok" });
  });

  it("Assistent: Einrichtungscode im Kopf, kein Passwort", async () => {
    useAuthStore.setState({ accessToken: null });
    const send = vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 201, text: JSON.stringify(STAGED) });
    await uploadBackup(file, { mode: "setup", setupCode: " ABCD-EFGH-JKMN " }, { onProgress: vi.fn() });
    const req = send.mock.calls[0][0];
    expect(req.url).toBe("/api/v1/auth/bootstrap/restore/upload");
    expect(req.headers).toEqual({ "X-Setup-Code": "ABCD-EFGH-JKMN" });
  });

  it("wirft den Text des Servers bei einem Fehler", async () => {
    vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 413, text: JSON.stringify({ detail: "Die Datei ist zu groß." }) });
    await expect(uploadBackup(file, { mode: "owner", accountPassword: "x" }, { onProgress: vi.fn() })).rejects.toMatchObject({ status: 413, message: "Die Datei ist zu groß." });
  });

  it("Owner: bei 401 wird der Zugang einmal erneuert und der Upload wiederholt", async () => {
    const send = vi.spyOn(uploadTransport, "send")
      .mockResolvedValueOnce({ status: 401, text: "{}" })
      .mockResolvedValueOnce({ status: 201, text: JSON.stringify(STAGED) });
    const refresh = vi.fn(async () => { useAuthStore.setState({ accessToken: "neu" }); return true; });
    useAuthStore.setState({ refresh } as never);
    await uploadBackup(file, { mode: "owner", accountPassword: "x" }, { onProgress: vi.fn() });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[1][0].headers.Authorization).toBe("Bearer neu");
  });
});

describe("Aufrufe", () => {
  it("Owner: prüfen, vormerken, abbrechen und neu starten laufen unter /system", async () => {
    const calls = stubFetch(() => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    await inspectBackup(STAGED.id, { password: "geheim" }, { mode: "owner", accountPassword: "x" });
    await scheduleRestore(STAGED.id, { mode: "owner", accountPassword: "konto" });
    await restartServer({ mode: "owner", accountPassword: "konto" });
    await cancelRestore({ mode: "owner", accountPassword: "" });
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      `POST /system/restore/${STAGED.id}/inspect`, `POST /system/restore/${STAGED.id}/schedule`, "POST /system/restart", "DELETE /system/restore/pending",
    ]);
    expect(calls[0].body).toEqual({ password: "geheim" });
    expect(calls[1].body).toEqual({ current_password: "konto", sign_out_all: true });
    expect(calls[2].body).toEqual({ current_password: "konto" });
    expect(calls.every((c) => !("x-setup-code" in c.headers))).toBe(true);
  });

  it("Assistent: jeder Aufruf trägt den Einrichtungscode, nie ein Konto-Passwort", async () => {
    const calls = stubFetch(() => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    const auth = { mode: "setup", setupCode: "ABCD-EFGH-JKMN" } as const;
    await inspectBackup(STAGED.id, { recovery_key: "AGE-SECRET-KEY-1X" }, auth);
    await scheduleRestore(STAGED.id, auth);
    await restartServer(auth);
    await cancelRestore(auth);
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      `POST /auth/bootstrap/restore/${STAGED.id}/inspect`, `POST /auth/bootstrap/restore/${STAGED.id}/schedule`, "POST /auth/bootstrap/restart",
      "DELETE /auth/bootstrap/restore/pending",
    ]);
    for (const call of calls) expect(call.headers["x-setup-code"]).toBe("ABCD-EFGH-JKMN");
    expect(calls[0].body).toEqual({ recovery_key: "AGE-SECRET-KEY-1X" });
    expect(calls[1].body).toEqual({ sign_out_all: true });
    expect(JSON.stringify(calls.map((c) => c.body))).not.toContain("current_password");
  });
});

describe("Warten auf den Neustart", () => {
  it("wartet, bis die Abfrage einmal gescheitert und danach wieder in Ordnung ist", async () => {
    const answers: ({ status: string; uptime_s: number } | null)[] = [{ status: "ok", uptime_s: 500 }, null, null, { status: "ok", uptime_s: 1 }];
    vi.spyOn(restartProbe, "health").mockImplementation(async () => (answers.length ? answers.shift()! : { status: "ok", uptime_s: 5 }));
    restartTiming.timeoutMs = 5000;
    expect(await waitForRestart(500)).toBe(true);
    expect(answers).toHaveLength(0);
  });

  it("eine Antwort VOR dem Neustart zählt nicht", async () => {
    const health = vi.spyOn(restartProbe, "health").mockResolvedValue({ status: "ok", uptime_s: 900 });
    restartTiming.timeoutMs = 60;
    expect(await waitForRestart(500)).toBe(false);
    expect(health).toHaveBeenCalled();
  });

  it("erkennt einen schnellen Neustart an der kleineren Laufzeit", async () => {
    vi.spyOn(restartProbe, "health").mockResolvedValue({ status: "ok", uptime_s: 2 });
    restartTiming.timeoutMs = 5000;
    expect(await waitForRestart(500)).toBe(true);
  });

  it("ohne bekannte Laufzeit zählt nur das Ausbleiben der Antwort", async () => {
    const answers: ({ status: string; uptime_s: number } | null)[] = [{ status: "ok", uptime_s: 3 }, null, { status: "ok", uptime_s: 1 }];
    vi.spyOn(restartProbe, "health").mockImplementation(async () => (answers.length ? answers.shift()! : { status: "ok", uptime_s: 3 }));
    restartTiming.timeoutMs = 5000;
    expect(await waitForRestart(null)).toBe(true);
  });

  it("bricht auf Wunsch ab", async () => {
    vi.spyOn(restartProbe, "health").mockResolvedValue(null);
    const controller = new AbortController();
    controller.abort();
    expect(await waitForRestart(1, { signal: controller.signal })).toBe(false);
  });

  it("liest Laufzeit und Zustand nach dem Neustart", async () => {
    stubFetch((call) => new Response(JSON.stringify(call.path === "/health" ? { status: "ok", uptime_s: 12.5 } : { needed: true, restore: { ok: false, message: "kaputt", at: null } }), { status: 200 }));
    expect(await readUptime()).toBe(12.5);
    expect(await bootstrapAfterRestart()).toEqual({ needed: true, restore: { ok: false, message: "kaputt", at: null } });
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }));
    expect(await readUptime()).toBeNull();
    expect(await bootstrapAfterRestart()).toBeNull();
  });
});
