import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { refreshAccessToken, refreshAccessTokenResult } from "./extensionToken";

const BODY = {
  access_token: "tok-neu",
  user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
};

beforeEach(() => {
  useAuthStore.setState({ accessToken: "tok-alt", user: null, status: "authenticated", mfaToken: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("refreshAccessToken", () => {
  it("liefert das neue Token", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(BODY), { status: 200 })));
    await expect(refreshAccessToken()).resolves.toBe("tok-neu");
    expect(useAuthStore.getState().accessToken).toBe("tok-neu");
  });

  it("hängt sich bei parallelen Aufrufen an denselben Refresh an", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(BODY), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const tokens = await Promise.all([refreshAccessToken(), refreshAccessToken(), refreshAccessToken()]);
    expect(tokens).toEqual(["tok-neu", "tok-neu", "tok-neu"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("liefert null, wenn der Server den Refresh ablehnt", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    await expect(refreshAccessToken()).resolves.toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  // Alte Schnittstelle: wirft nie, auch wenn der Server nicht antwortet -- aeltere Bundles
  // rufen es ohne try/catch auf.
  it("liefert null bei einem Netzwerkfehler, die Anmeldung bleibt", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(refreshAccessToken()).resolves.toBeNull();
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  it.each([502, 503, 504])("liefert null bei HTTP %i, die Anmeldung bleibt (Deploy-Neustart)", async (code) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad Gateway", { status: code })));
    await expect(refreshAccessToken()).resolves.toBeNull();
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });
});

// Unterscheidet "abgelehnt" und "nicht erreichbar": die Extension-Seite soll dann "Server gerade
// nicht erreichbar" zeigen statt "Nicht authentifiziert" (extensions/_shared/frontend/src/api.ts).
describe("refreshAccessTokenResult", () => {
  it("liefert das neue Token", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(BODY), { status: 200 })));
    await expect(refreshAccessTokenResult()).resolves.toEqual({ status: "ok", token: "tok-neu" });
  });

  it("meldet „abgelehnt“, wenn der Server den Refresh ablehnt", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    await expect(refreshAccessTokenResult()).resolves.toEqual({ status: "rejected" });
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("meldet „nicht erreichbar“ bei einem Netzwerkfehler, ohne zu werfen; die Anmeldung bleibt", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(refreshAccessTokenResult()).resolves.toEqual({ status: "unavailable" });
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  it.each([502, 503, 504])("meldet „nicht erreichbar“ bei HTTP %i (Deploy-Neustart)", async (code) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad Gateway", { status: code })));
    await expect(refreshAccessTokenResult()).resolves.toEqual({ status: "unavailable" });
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  // Der Server hat geantwortet, aber mit einem echten Fehler: Status und Grund werden mitgegeben
  // (`status` bleibt "unavailable", damit aeltere Seiten weiter "nicht erreichbar" zeigen).
  it("gibt bei 507 mit Grund den HTTP-Status und den Grund mit; die Anmeldung bleibt", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Speicherplatz voll" }), { status: 507 })));
    await expect(refreshAccessTokenResult()).resolves.toEqual({
      status: "unavailable",
      httpStatus: 507,
      message: "Speicherplatz voll",
    });
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  it("gibt bei 500 ohne lesbaren Grund „HTTP 500“ mit", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Internal Server Error", { status: 500 })));
    await expect(refreshAccessTokenResult()).resolves.toEqual({ status: "unavailable", httpStatus: 500, message: "HTTP 500" });
  });

  it("502 mit HTML vom Proxy bleibt ohne Status und Grund", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: 502 })));
    const result = await refreshAccessTokenResult();
    expect(result).toEqual({ status: "unavailable" });
    expect(result).not.toHaveProperty("httpStatus");
    expect(result).not.toHaveProperty("message");
  });

  it("502 mit Grund des Backends gilt als Antwort des Servers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Datenbank gesperrt" }), { status: 502 })));
    await expect(refreshAccessTokenResult()).resolves.toEqual({ status: "unavailable", httpStatus: 502, message: "Datenbank gesperrt" });
  });
});
