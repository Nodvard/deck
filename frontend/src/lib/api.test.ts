import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { api, ApiError } from "./api";

function respondOnce(body: unknown, status: number) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status })));
}

async function thrown(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("Anfrage hätte fehlschlagen müssen");
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Die Fehlermeldung kam nur aus `detail` -- eine Gate-Sperre (403 mit
 * ActionOut als Body, api/v1/hosts.py) wurde zu "HTTP 403", ein FastAPI-
 * Validierungsfehler (422, `detail` ist eine Liste) zu "[object Object]".
 */
describe("apiFetch Fehlermeldungen", () => {
  it("Text aus detail wie bisher", async () => {
    respondOnce({ detail: "Unbekannter Host." }, 404);
    const err = await thrown(api.get("/hosts/x"));
    expect(err.status).toBe(404);
    expect(err.message).toBe("Unbekannter Host.");
  });

  it("Gate-Sperre: Begründung aus gate_decision.detail, Body bleibt am Fehler", async () => {
    const body = {
      id: "a1", status: "denied", result: {},
      gate_decision: { rule: "flap_limit", detail: "Bereits mehrfach in kurzer Zeit versucht (Fingerprint abc...)." },
    };
    respondOnce(body, 403);
    const err = await thrown(api.post("/hosts/h1/actions/vm.reboot", { payload: {} }));
    expect(err.status).toBe(403);
    expect(err.message).toBe("Bereits mehrfach in kurzer Zeit versucht (Fingerprint abc...).");
    expect(err.detail).toEqual(body);
  });

  it("Validierungsfehler (422) als lesbarer Text statt [object Object]", async () => {
    respondOnce({
      detail: [
        { type: "missing", loc: ["body", "payload", "snapname"], msg: "Field required", input: null },
        { type: "string_type", loc: ["query", "limit"], msg: "Input should be a valid integer" },
      ],
    }, 422);
    const err = await thrown(api.post("/hosts/h1/actions/vm.snapshot_rollback", {}));
    expect(err.message).toBe("payload.snapname: Field required; limit: Input should be a valid integer");
  });

  it("ohne verwertbaren Body bleibt es bei HTTP <Status>", async () => {
    respondOnce(undefined, 500);
    expect((await thrown(api.get("/x"))).message).toBe("HTTP 500");
    respondOnce({ status: "denied", gate_decision: { rule: "flap_limit" } }, 403);
    expect((await thrown(api.get("/x"))).message).toBe("HTTP 403");
  });
});

/**
 * Waehrend der Container nach einem Deploy neu startet, ist das Token meist noch gueltig:
 * normale Anfragen bekommen dann gar keine Antwort (TypeError von fetch) oder eine
 * Fehlerseite vom Proxy (502/503/504) -- beides soll die verstaendliche Meldung zeigen
 * statt "Failed to fetch" oder "HTTP 502".
 */
describe("apiFetch ohne Antwort des Servers", () => {
  const UNAVAILABLE = "Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.";

  it("Netzwerkfehler: ApiError 503 mit klarer Meldung, weiter angemeldet", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

    const err = await thrown(api.get("/hosts"));

    expect(err.status).toBe(503);
    expect(err.message).toBe(UNAVAILABLE);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok");
  });

  it("Netzwerkfehler auch beim Wiederholen nach dem Erneuern", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/auth/refresh") {
        return new Response(JSON.stringify({ access_token: "tok-neu", user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] } }), { status: 200 });
      }
      calls += 1;
      if (calls === 1) return new Response(JSON.stringify({ detail: "Nicht authentifiziert" }), { status: 401 });
      throw new TypeError("Failed to fetch");
    }));

    const err = await thrown(api.get("/hosts"));

    expect(err.status).toBe(503);
    expect(err.message).toBe(UNAVAILABLE);
  });

  it.each([502, 503, 504])("%i mit Fehlerseite vom Proxy (kein JSON): klare Meldung", async (code) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: code })));

    const err = await thrown(api.get("/hosts"));

    expect(err.status).toBe(code);
    expect(err.message).toBe(UNAVAILABLE);
    expect(err.detail).toBe("<html>Bad Gateway</html>");
  });

  it("503 mit Text vom Server: der Text des Servers bleibt", async () => {
    respondOnce({ detail: "Datenbank wird gesichert." }, 503);
    expect((await thrown(api.get("/hosts"))).message).toBe("Datenbank wird gesichert.");
  });

  it("500 bleibt bei HTTP 500 (kein Ausfall, sondern ein Fehler im Server)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("kaputt", { status: 500 })));
    expect((await thrown(api.get("/hosts"))).message).toBe("HTTP 500");
  });

  it("Abbruch (Seite verlassen) bleibt ein AbortError und wird nicht umgedeutet", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new DOMException("Abgebrochen", "AbortError"); }));

    await expect(api.get("/hosts")).rejects.toMatchObject({ name: "AbortError" });
  });
});

/**
 * Nach einem Deploy startet der Container neu: das Token ist abgelaufen (401), aber der
 * Refresh bekommt 502/503 oder gar keine Verbindung. Das darf nicht abmelden -- nur die
 * laufende Anfrage scheitert, mit einer verstaendlichen Meldung.
 */
describe("apiFetch bei 401 und nicht erreichbarem Server", () => {
  const USER = { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] };
  const UNAVAILABLE = "Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.";

  function stubApi(refresh: () => Response | Promise<Response>) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/auth/refresh") return refresh();
      return new Response(JSON.stringify({ detail: "Nicht authentifiziert" }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  beforeEach(() => {
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
  });

  it("Refresh bekommt 502: klare Meldung, weiter angemeldet", async () => {
    stubApi(() => new Response("Bad Gateway", { status: 502 }));

    const err = await thrown(api.get("/hosts"));

    expect(err.message).toBe(UNAVAILABLE);
    expect(err.status).toBe(503);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok");
  });

  it("Refresh bekommt 507 mit Grund (Platte voll): ApiError 507 mit dem Text des Servers, weiter angemeldet", async () => {
    const detail = "Der Speicherplatz auf dem Server ist voll. Bitte Platz schaffen.";
    stubApi(() => new Response(JSON.stringify({ detail }), { status: 507 }));

    const err = await thrown(api.get("/hosts"));

    expect(err.status).toBe(507);
    expect(err.message).toContain("Speicherplatz");
    expect(err.detail).toEqual({ detail });
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok");
  });

  it("Refresh bekommt 500 mit Grund: dessen Text statt \"nicht erreichbar\"", async () => {
    stubApi(() => new Response(JSON.stringify({ detail: "Interner Fehler beim Erneuern." }), { status: 500 }));

    const err = await thrown(api.get("/hosts"));

    expect(err.status).toBe(500);
    expect(err.message).toBe("Interner Fehler beim Erneuern.");
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("Refresh ohne Verbindung: dieselbe Meldung, weiter angemeldet", async () => {
    stubApi(() => { throw new TypeError("Failed to fetch"); });

    const err = await thrown(api.get("/hosts"));

    expect(err.message).toBe(UNAVAILABLE);
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("Server wieder da: die nächste Anfrage erneuert und klappt", async () => {
    let up = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v1/auth/refresh") {
        return up ? new Response(JSON.stringify({ access_token: "tok-neu", user: USER }), { status: 200 }) : new Response("", { status: 503 });
      }
      const auth = new Headers(init?.headers).get("Authorization");
      return auth === "Bearer tok-neu"
        ? new Response(JSON.stringify({ ok: true }), { status: 200 })
        : new Response(JSON.stringify({ detail: "Nicht authentifiziert" }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await thrown(api.get("/hosts"));
    up = true;
    await expect(api.get("/hosts")).resolves.toEqual({ ok: true });
    expect(useAuthStore.getState().accessToken).toBe("tok-neu");
  });

  it("Refresh abgelehnt (401): abgemeldet, die Anfrage meldet 401", async () => {
    stubApi(() => new Response(JSON.stringify({ detail: "Abgelaufen" }), { status: 401 }));

    const err = await thrown(api.get("/hosts"));

    expect(err.status).toBe(401);
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });
});
