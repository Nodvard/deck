import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { refreshAccessToken, refreshAccessTokenResult } from "../../../../frontend/src/lib/extensionToken";
import { SERVER_UNAVAILABLE_TEXT as CORE_SERVER_UNAVAILABLE_TEXT, useAuthStore } from "../../../../frontend/src/state/auth";

import { authedFetch, errorFromBody, errorText, SERVER_UNAVAILABLE_TEXT, ServerUnavailableError } from "./api";
import { formatBytes } from "./format";

let token: string | null;
/** `refreshAccessTokenResult` des Kerns. */
let refresh: ReturnType<typeof vi.fn>;
/** `refreshAccessToken` (alte Schnittstelle, auch aeltere Kerne): darf neben dem neuen nicht laufen. */
let legacyRefresh: ReturnType<typeof vi.fn>;

beforeEach(() => {
  token = "alt";
  refresh = vi.fn(async (): Promise<NodvardDeckTokenRefreshResult> => {
    token = "neu";
    return { status: "ok", token };
  });
  legacyRefresh = vi.fn(async () => {
    token = "neu";
    return token;
  });
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => token,
    refreshAccessToken: legacyRefresh as unknown as () => Promise<string | null>,
    refreshAccessTokenResult: refresh as unknown as () => Promise<NodvardDeckTokenRefreshResult>,
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn().mockResolvedValue(null),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Partial<Window>).__nodvardDeck;
});

function auth(call: unknown[]): string | null {
  return new Headers((call[1] as RequestInit).headers).get("Authorization");
}

describe("authedFetch", () => {
  it("haengt /api/v1 an, setzt den Bearer und den JSON-Typ nur bei Text-Body", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await authedFetch("/hosts", { method: "POST", body: "{}" });
    await authedFetch("/hosts");
    await authedFetch("/upload", { method: "POST", body: new Blob(["x"]), headers: { "Content-Type": "image/png" } });

    const [first, second, third] = fetchMock.mock.calls as unknown as unknown[][];
    expect(first[0]).toBe("/api/v1/hosts");
    expect(auth(first)).toBe("Bearer alt");
    expect(new Headers((first[1] as RequestInit).headers).get("Content-Type")).toBe("application/json");
    expect(new Headers((second[1] as RequestInit).headers).has("Content-Type")).toBe(false);
    expect(new Headers((third[1] as RequestInit).headers).get("Content-Type")).toBe("image/png");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("erneuert bei 401 einmal das Token und wiederholt mit dem neuen", async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) =>
      new Response("{}", { status: new Headers(init.headers).get("Authorization") === "Bearer neu" ? 200 : 401 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await authedFetch("/scripts/s1", { method: "PUT", body: '{"a":1}' });

    expect(res.status).toBe(200);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(legacyRefresh).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBe('{"a":1}');
  });

  it("wiederholt nur einmal: bleibt es beim 401, kommt dieses zurueck", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await authedFetch("/hosts");

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("liefert das 401, wenn der Server die Anmeldung ablehnt", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: "Nicht authentifiziert." }), { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    refresh.mockResolvedValueOnce({ status: "rejected" });
    const res = await authedFetch("/hosts");
    expect(res.status).toBe(401);
    expect(await errorText(res)).toBe("Nicht authentifiziert.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("kann der Kern gerade nicht erneuern (Server antwortet nicht), heißt es „nicht erreichbar“ statt 401", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    refresh.mockResolvedValueOnce({ status: "unavailable" });

    const caught = await authedFetch("/hosts").catch((err: unknown) => err);
    expect(caught).toBeInstanceOf(ServerUnavailableError);
    expect((caught as Error).message).toBe(SERVER_UNAVAILABLE_TEXT);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(legacyRefresh).not.toHaveBeenCalled();
  });

  it("Server antwortet beim Erneuern mit einem Fehler (507): der Fehlertext nennt den Grund", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    refresh.mockResolvedValueOnce({ status: "unavailable", httpStatus: 507, message: "Speicherplatz voll" });

    const caught = await authedFetch("/hosts").catch((err: unknown) => err);
    expect(caught).toBeInstanceOf(ServerUnavailableError);
    expect((caught as Error).message).toContain("Speicherplatz voll");
    expect((caught as Error).message).toBe("Server meldet: Speicherplatz voll");
    expect((caught as ServerUnavailableError).status).toBe(507);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(legacyRefresh).not.toHaveBeenCalled();
  });

  it("älterer Kern ohne message im Ergebnis: unverändert „nicht erreichbar“", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    refresh.mockResolvedValueOnce({ status: "unavailable" });

    const caught = await authedFetch("/hosts").catch((err: unknown) => err);
    expect(caught).toBeInstanceOf(ServerUnavailableError);
    expect((caught as Error).message).toBe(SERVER_UNAVAILABLE_TEXT);
    expect((caught as ServerUnavailableError).status).toBeUndefined();
  });

  it("älterer Kern ohne refreshAccessTokenResult: erneuert über refreshAccessToken, null bleibt beim 401", async () => {
    delete window.__lattice.refreshAccessTokenResult;
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) =>
      new Response("{}", { status: new Headers(init.headers).get("Authorization") === "Bearer neu" ? 200 : 401 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    expect((await authedFetch("/hosts")).status).toBe(200);
    expect(legacyRefresh).toHaveBeenCalledTimes(1);

    token = "alt";
    legacyRefresh.mockResolvedValueOnce(null);
    expect((await authedFetch("/hosts")).status).toBe(401);
  });

  it("Netzwerkfehler (Server startet neu) wirft die Meldung des Kerns statt „Failed to fetch“", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

    const caught = await authedFetch("/hosts").catch((err: unknown) => err);
    expect(caught).toBeInstanceOf(ServerUnavailableError);
    expect((caught as Error).message).toBe("Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("ein Abbruch (Seite verlassen) bleibt ein AbortError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new DOMException("Abgebrochen", "AbortError"); }));

    const caught = await authedFetch("/hosts").catch((err: unknown) => err);
    expect(caught).toBeInstanceOf(DOMException);
    expect((caught as DOMException).name).toBe("AbortError");
  });

  it.each([502, 503, 504])("HTTP %i ohne Grund (Proxy, Server läuft nicht) wirft „nicht erreichbar“", async (code) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: code })));

    await expect(authedFetch("/hosts")).rejects.toThrow(SERVER_UNAVAILABLE_TEXT);
  });

  it("eine 502 des Backends mit Grund (Zielserver nicht erreichbar) bleibt eine normale Antwort", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Nicht erreichbar: timeout" }), { status: 502 })));

    const res = await authedFetch("/ext/system/hosts/h1/info");
    expect(res.status).toBe(502);
    expect(await errorText(res)).toBe("Nicht erreichbar: timeout");
  });

  it("nimmt ein zwischenzeitlich erneuertes Token, ohne selbst zu erneuern", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async () => {
      calls += 1;
      if (calls === 1) token = "von-anderem-aufruf";
      return new Response("{}", { status: calls === 1 ? 401 : 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await authedFetch("/hosts");

    expect(res.status).toBe(200);
    expect(refresh).not.toHaveBeenCalled();
    expect(auth(fetchMock.mock.calls[1] as unknown[])).toBe("Bearer von-anderem-aufruf");
  });

  it("kommt ohne Erneuern (ganz alter Kern) mit dem 401 zurecht", async () => {
    delete window.__lattice.refreshAccessToken;
    delete window.__lattice.refreshAccessTokenResult;
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await authedFetch("/hosts")).status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * Die Kern-Shell hat zwei Namen (`window.__nodvardDeck`, alt `window.__lattice`): das Kit findet
 * sie unter beiden -- auch ein neues Bundle in einem Tab mit aelterem Kern (nur `__lattice`) und
 * ein Fremd-Kern, der nur den neuen Namen setzt.
 */
describe("authedFetch findet die Kern-Shell unter beiden Namen", () => {
  function renewing401() {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) =>
      new Response("{}", { status: new Headers(init.headers).get("Authorization") === "Bearer neu" ? 200 : 401 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it.each([
    ["nur __lattice (aelterer Kern)", (shell: NodvardDeckShell) => { window.__lattice = shell; }],
    ["nur __nodvardDeck", (shell: NodvardDeckShell) => { delete (window as Partial<Window>).__lattice; window.__nodvardDeck = shell; }],
    ["beide Namen, dasselbe Objekt (neuer Kern)", (shell: NodvardDeckShell) => { window.__lattice = shell; window.__nodvardDeck = shell; }],
  ])("%s: Bearer, Erneuern bei 401 und Wiederholen", async (_name, install) => {
    install(window.__lattice);
    const fetchMock = renewing401();

    const res = await authedFetch("/hosts");

    expect(res.status).toBe(200);
    expect(auth(fetchMock.mock.calls[0] as unknown[])).toBe("Bearer alt");
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(auth(fetchMock.mock.calls[1] as unknown[])).toBe("Bearer neu");
  });

  it("sind die Namen verschieden belegt (Fremd-Kern), gilt der neue", async () => {
    const neu = { ...window.__lattice, getAccessToken: () => "vom-neuen" } as NodvardDeckShell;
    window.__nodvardDeck = neu;
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await authedFetch("/hosts");

    expect(auth(fetchMock.mock.calls[0] as unknown[])).toBe("Bearer vom-neuen");
  });
});

/**
 * Mit dem echten Erneuern des Kerns (lib/extensionToken.ts -> state/auth.ts), so wie die Shell
 * es in `window.__nodvardDeck` legt: genau der Fall aus dem Deploy -- das Token läuft ab, während
 * der Container neu startet.
 */
describe("authedFetch mit dem Erneuern des Kerns", () => {
  const USER = { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] };

  beforeEach(() => {
    useAuthStore.setState({ accessToken: "alt", user: USER, status: "authenticated", mfaToken: null });
    window.__lattice.getAccessToken = () => useAuthStore.getState().accessToken;
    window.__lattice.refreshAccessToken = refreshAccessToken;
    window.__lattice.refreshAccessTokenResult = refreshAccessTokenResult;
  });

  /** `/api/v1/hosts` braucht das Token "neu"; `/api/v1/auth/refresh` antwortet mit `refreshReply`. */
  function stub(refreshReply: () => Response) {
    const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
      if (url === "/api/v1/auth/refresh") return refreshReply();
      const ok = new Headers(init.headers).get("Authorization") === "Bearer neu";
      return ok ? new Response("[]", { status: 200 }) : new Response(JSON.stringify({ detail: "Nicht authentifiziert." }), { status: 401 });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("401, Erneuern klappt: einmal wiederholt, mit dem neuen Token", async () => {
    const fetchMock = stub(() => new Response(JSON.stringify({ access_token: "neu", user: USER }), { status: 200 }));

    const res = await authedFetch("/hosts");

    expect(res.status).toBe(200);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/api/v1/hosts", "/api/v1/auth/refresh", "/api/v1/hosts"]);
  });

  it("401, der Server lehnt das Erneuern ab (401): es bleibt bei „Nicht authentifiziert“", async () => {
    stub(() => new Response("{}", { status: 401 }));

    const res = await authedFetch("/hosts");

    expect(res.status).toBe(401);
    expect(await errorText(res)).toBe("Nicht authentifiziert.");
    expect(useAuthStore.getState().status).toBe("anonymous"); // die Shell zeigt die Anmeldung
  });

  it("401, beim Erneuern antwortet der Server mit 507 und Grund: der Fehler nennt ihn, die Anmeldung bleibt", async () => {
    stub(() => new Response(JSON.stringify({ detail: "Speicherplatz voll" }), { status: 507 }));

    const caught = await authedFetch("/hosts").catch((err: unknown) => err);

    expect(caught).toBeInstanceOf(ServerUnavailableError);
    expect((caught as Error).message).toBe("Server meldet: Speicherplatz voll");
    expect((caught as ServerUnavailableError).status).toBe(507);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("alt");
  });

  it.each([
    ["Netzwerkfehler", () => { throw new TypeError("Failed to fetch"); }],
    ["502 vom Proxy", () => new Response("Bad Gateway", { status: 502 })],
  ])("401, beim Erneuern %s (Container startet neu): „nicht erreichbar“, die Anmeldung bleibt", async (_label, reply) => {
    stub(reply);

    await expect(authedFetch("/hosts")).rejects.toThrow(SERVER_UNAVAILABLE_TEXT);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("alt");
  });
});

describe("SERVER_UNAVAILABLE_TEXT", () => {
  it("ist dieselbe Meldung wie im Kern", () => {
    expect(SERVER_UNAVAILABLE_TEXT).toBe(CORE_SERVER_UNAVAILABLE_TEXT);
  });
});

describe("errorText", () => {
  it("nimmt detail als Text, als Liste und den Grund der Gate-Entscheidung", async () => {
    expect(errorFromBody({ detail: "Unbekannter Host." }, 404)).toBe("Unbekannter Host.");
    expect(errorFromBody({ detail: [{ msg: "Feld fehlt" }, { msg: "Zahl erwartet" }] }, 422)).toBe("Feld fehlt; Zahl erwartet");
    expect(errorFromBody({ status: "denied", gate_decision: { rule: "blocklist", detail: "Server gesperrt" } }, 403)).toBe("Server gesperrt");
    expect(errorFromBody(null, 500)).toBe("HTTP 500");
    expect(errorFromBody({ detail: [] }, 422)).toBe("HTTP 422");
    expect(await errorText(new Response(JSON.stringify({ detail: "Nein." }), { status: 400 }))).toBe("Nein.");
  });

  it("502/503/504 ohne Grund: dieselbe Meldung wie im Kern statt „HTTP 502“", async () => {
    expect(errorFromBody({}, 502)).toBe(SERVER_UNAVAILABLE_TEXT);
    expect(await errorText(new Response("kein json", { status: 503 }))).toBe(SERVER_UNAVAILABLE_TEXT);
    expect(errorFromBody(null, 504)).toBe(SERVER_UNAVAILABLE_TEXT);
    expect(errorFromBody({ detail: "Nicht erreichbar: timeout" }, 502)).toBe("Nicht erreichbar: timeout");
  });
});

describe("formatBytes", () => {
  it("skaliert in 1024er-Schritten", () => {
    expect(formatBytes(null)).toBe("?");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(45 * 1024 ** 2)).toBe("45 MB");
    expect(formatBytes(8.5 * 1024 ** 3)).toBe("8.5 GB");
    expect(formatBytes(93 * 1024 ** 3)).toBe("93 GB");
  });
  it("fixed zeigt immer eine Nachkommastelle", () => {
    expect(formatBytes(0, { fixed: true })).toBe("0 B");
    expect(formatBytes(2 * 1024 ** 3, { fixed: true })).toBe("2.0 GB");
    expect(formatBytes(45 * 1024 ** 3, { fixed: true })).toBe("45.0 GB");
  });
});
