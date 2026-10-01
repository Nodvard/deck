import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { refreshAfterMs, retryDelayMs, tokenLifetimeMs, useProactiveTokenRefresh } from "./tokenRefresh";

const USER = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] };
const MIN = 60_000;

/** JWT wie core/security.py::create_jwt (nur der Inhalt zaehlt, die Signatur prueft der Client nie). */
function jwt(claims: Record<string, unknown>): string {
  const b64 = (value: unknown) => btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64(claims)}.signatur`;
}

/** Access-Token mit 15 min Laufzeit, ausgestellt "jetzt" nach der Uhr des Servers. */
function serverToken(n: number, serverNowS = Math.floor(Date.now() / 1000)): string {
  return jwt({ sub: "u1", typ: "access", iat: serverNowS, exp: serverNowS + 15 * 60, jti: `j${n}` });
}

let visibility: DocumentVisibilityState = "visible";
let refreshCalls = 0;

function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  refreshCalls = 0;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) !== "/api/v1/auth/refresh") throw new Error(`Unerwarteter Fetch: ${String(input)}`);
    refreshCalls += 1;
    return new Response(JSON.stringify({ access_token: serverToken(100 + refreshCalls), user: USER }), { status: 200 });
  }));
  useAuthStore.setState({ accessToken: serverToken(1), user: USER, status: "authenticated", mfaToken: null });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("tokenLifetimeMs / refreshAfterMs", () => {
  it("Laufzeit aus exp - iat des JWT, sonst 15 min", () => {
    expect(tokenLifetimeMs(jwt({ iat: 1000, exp: 1000 + 600 }))).toBe(10 * MIN);
    expect(tokenLifetimeMs("kein-jwt")).toBe(15 * MIN);
    expect(tokenLifetimeMs(jwt({ exp: 5 }))).toBe(15 * MIN);
  });

  it("2 min vor Ablauf, bei sehr kurzen Tokens zur Hälfte der Laufzeit", () => {
    expect(refreshAfterMs(15 * MIN)).toBe(13 * MIN);
    expect(refreshAfterMs(MIN)).toBe(MIN / 2);
  });
});

describe("retryDelayMs", () => {
  it("5 s, 15 s, 30 s, danach jede Minute", () => {
    expect([0, 1, 2, 3, 4, 10].map(retryDelayMs)).toEqual([5_000, 15_000, 30_000, 60_000, 60_000, 60_000]);
  });
});

describe("useProactiveTokenRefresh", () => {
  it("sichtbarer Tab: erneuert 2 min vor Ablauf, danach wieder erst 13 min später", async () => {
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(13 * MIN - 1000);
    expect(refreshCalls).toBe(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(refreshCalls).toBe(1);
    expect(useAuthStore.getState().accessToken).toBe(serverToken(101));

    await vi.advanceTimersByTimeAsync(13 * MIN - 1000);
    expect(refreshCalls).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(refreshCalls).toBe(2);
  });

  it("Hintergrund-Tab erneuert nie; beim Zurückkommen sofort, wenn das Token (fast) abgelaufen ist", async () => {
    visibility = "hidden";
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(40 * MIN);
    expect(refreshCalls).toBe(0);

    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCalls).toBe(1);

    // Kurz weg und wieder da: das neue Token ist frisch -> keine weitere Erneuerung.
    setVisibility("hidden");
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCalls).toBe(1);
  });

  it("Zurückkommen, solange das Token noch lange gilt: keine Erneuerung", async () => {
    renderHook(() => useProactiveTokenRefresh());
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(5 * MIN);
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCalls).toBe(0);
  });

  it("falsch gehende Browser-Uhr: kein sofortiger Refresh in Schleife", async () => {
    // Server-Uhr 1 h hinter dem Browser: exp liegt nach Browser-Uhr schon in der Vergangenheit.
    useAuthStore.setState({ accessToken: serverToken(1, Math.floor(Date.now() / 1000) - 3600) });
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(MIN);
    expect(refreshCalls).toBe(0);
    await vi.advanceTimersByTimeAsync(12 * MIN);
    expect(refreshCalls).toBe(1);
  });

  it("Server nicht erreichbar (Netzwerkfehler): angemeldet bleiben, neuer Versuch nach 5 s", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async () => {
      refreshCalls += 1;
      if (fail) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify({ access_token: serverToken(200), user: USER }), { status: 200 });
    }));
    const original = useAuthStore.getState().accessToken;
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(13 * MIN);
    expect(refreshCalls).toBe(1);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe(original);

    fail = false;
    await vi.advanceTimersByTimeAsync(5_000 - 1);
    expect(refreshCalls).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(refreshCalls).toBe(2);
    expect(useAuthStore.getState().accessToken).toBe(serverToken(200));
  });

  it("502 beim Container-Neustart: nicht abmelden, Pausen 5 s, 15 s, 30 s, dann jede Minute, bis es klappt", async () => {
    let up = false;
    vi.stubGlobal("fetch", vi.fn(async () => {
      refreshCalls += 1;
      if (!up) return new Response("Bad Gateway", { status: 502 });
      return new Response(JSON.stringify({ access_token: serverToken(300), user: USER }), { status: 200 });
    }));
    const original = useAuthStore.getState().accessToken;
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(13 * MIN);
    expect(refreshCalls).toBe(1);

    // 5 s -> 15 s -> 30 s -> 60 s -> 60 s
    for (const [wait, calls] of [[5_000, 2], [15_000, 3], [30_000, 4], [60_000, 5], [60_000, 6]] as const) {
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(refreshCalls).toBe(calls - 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(refreshCalls).toBe(calls);
      expect(useAuthStore.getState().status).toBe("authenticated");
      expect(useAuthStore.getState().accessToken).toBe(original);
    }

    up = true;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(refreshCalls).toBe(7);
    expect(useAuthStore.getState().accessToken).toBe(serverToken(300));
    expect(useAuthStore.getState().status).toBe("authenticated");

    // Erfolg setzt den Takt zurueck: naechster Versuch wieder regulaer nach 13 min, keine Wiederholungen.
    await vi.advanceTimersByTimeAsync(13 * MIN - 1000);
    expect(refreshCalls).toBe(7);
    await vi.advanceTimersByTimeAsync(1000);
    expect(refreshCalls).toBe(8);
  });

  it("Server lehnt das Cookie ab (401): abgemeldet, keine Wiederholungen", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      refreshCalls += 1;
      return new Response(JSON.stringify({ detail: "Ungültig." }), { status: 401 });
    }));
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(13 * MIN);
    expect(refreshCalls).toBe(1);
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().accessToken).toBeNull();

    await vi.advanceTimersByTimeAsync(10 * MIN);
    expect(refreshCalls).toBe(1);
  });

  it("Hintergrund-Tab: keine Wiederholungen, beim Zurückkommen sofort ein Versuch (ohne doppelten Timer)", async () => {
    let up = false;
    vi.stubGlobal("fetch", vi.fn(async () => {
      refreshCalls += 1;
      if (!up) return new Response("", { status: 503 });
      return new Response(JSON.stringify({ access_token: serverToken(400), user: USER }), { status: 200 });
    }));
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(13 * MIN);
    expect(refreshCalls).toBe(1);

    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(10 * MIN);
    expect(refreshCalls).toBe(1);
    expect(useAuthStore.getState().status).toBe("authenticated");

    // Zurueck im Tab, Server immer noch weg: sofort ein Versuch, danach die naechste Pause (15 s).
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCalls).toBe(2);
    await vi.advanceTimersByTimeAsync(15_000 - 1);
    expect(refreshCalls).toBe(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(refreshCalls).toBe(3);

    // Tab-Wechsel waehrend eine Wiederholung ansteht: kein zweiter Timer daneben.
    up = true;
    setVisibility("hidden");
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCalls).toBe(4);
    expect(useAuthStore.getState().accessToken).toBe(serverToken(400));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(refreshCalls).toBe(4);
  });

  it("ohne Token (abgemeldet) passiert nichts", async () => {
    useAuthStore.setState({ accessToken: null, user: null, status: "anonymous" });
    renderHook(() => useProactiveTokenRefresh());
    await vi.advanceTimersByTimeAsync(60 * MIN);
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCalls).toBe(0);
  });
});
