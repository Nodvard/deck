import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REFRESH_TIMEOUT_MS, SERVER_UNAVAILABLE_TEXT, ServerUnavailableError, useAuthStore } from "./auth";

const SUCCESS_BODY = {
  access_token: "tok-1",
  user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
};

beforeEach(() => {
  useAuthStore.setState({ accessToken: null, user: null, status: "unknown", mfaToken: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Regressionstest für das Refresh-Token-Race (live als deterministisch bei jeder
 * Erstinstallation bewiesen, hier root-cause-gefixt) --
 * siehe den `inFlightRefresh`-Kommentar in auth.ts für die volle Begründung.
 * `services/auth.py::refresh_access_token()` rotiert das Refresh-Token bei jeder
 * Einlösung; zwei gleichzeitige Client-Aufrufe mit demselben Cookie dürfen deshalb
 * NICHT zwei Anfragen an den Server schicken -- nur die erste wäre gültig, die
 * zweite bekäme fälschlich 401.
 */
describe("useAuthStore.refresh() -- geteilter In-Flight-Zustand", () => {
  it("loest bei zwei gleichzeitigen Aufrufen nur EINE Netzwerkanfrage aus", async () => {
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        return new Response(JSON.stringify(SUCCESS_BODY), { status: 200 });
      }),
    );

    const first = useAuthStore.getState().refresh();
    const second = useAuthStore.getState().refresh();

    const [firstResult, secondResult] = await Promise.all([first, second]);

    expect(callCount).toBe(1);
    expect(firstResult).toBe(true);
    expect(secondResult).toBe(true);
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("erlaubt einen NEUEN Refresh, nachdem der vorherige abgeschlossen ist", async () => {
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        return new Response(JSON.stringify(SUCCESS_BODY), { status: 200 });
      }),
    );

    await useAuthStore.getState().refresh();
    await useAuthStore.getState().refresh();

    expect(callCount).toBe(2);
  });

  it("teilt auch einen fehlschlagenden Refresh (kein falsches Erfolgsergebnis für den zweiten Aufrufer)", async () => {
    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        callCount += 1;
        return new Response(JSON.stringify({ detail: "abgelaufen" }), { status: 401 });
      }),
    );

    const [firstResult, secondResult] = await Promise.all([
      useAuthStore.getState().refresh(),
      useAuthStore.getState().refresh(),
    ]);

    expect(callCount).toBe(1);
    expect(firstResult).toBe(false);
    expect(secondResult).toBe(false);
    expect(useAuthStore.getState().status).toBe("anonymous");
  });
});

/**
 * Deploy: der Container startet neu, der Proxy (oder der Browser selbst) liefert kurz
 * 502/503/504 bzw. einen Netzwerkfehler. Das darf niemanden abmelden -- nur ein Server,
 * der das Refresh-Cookie AUSDRUECKLICH ablehnt (400/401/403), beendet die Anmeldung.
 */
describe("useAuthStore.refresh() -- Server nicht erreichbar", () => {
  function signedIn() {
    useAuthStore.setState({ accessToken: "tok-alt", user: SUCCESS_BODY.user, status: "authenticated", mfaToken: null });
  }

  it.each([500, 502, 503, 504, 429, 404])("HTTP %i: Anmeldung bleibt, refresh() wirft ServerUnavailableError", async (code) => {
    signedIn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: code })));

    await expect(useAuthStore.getState().refresh()).rejects.toBeInstanceOf(ServerUnavailableError);

    const state = useAuthStore.getState();
    expect(state.status).toBe("authenticated");
    expect(state.accessToken).toBe("tok-alt");
    expect(state.user).toEqual(SUCCESS_BODY.user);
  });

  it("507 mit Grund (Platte voll): Anmeldung bleibt, Fehler nennt Status und Text des Servers", async () => {
    signedIn();
    const detail = "Der Speicherplatz auf dem Server ist voll. Bitte Platz schaffen.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail }), { status: 507 })));

    const err = await useAuthStore.getState().refresh().catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ServerUnavailableError);
    expect((err as ServerUnavailableError).status).toBe(507);
    expect((err as ServerUnavailableError).message).toContain("Speicherplatz");
    expect((err as ServerUnavailableError).detail).toEqual({ detail });
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  it("500 ohne lesbaren Grund: Status und \"HTTP 500\" statt \"nicht erreichbar\"", async () => {
    signedIn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Internal Server Error", { status: 500 })));

    const err = (await useAuthStore.getState().refresh().catch((e: unknown) => e)) as ServerUnavailableError;

    expect(err.status).toBe(500);
    expect(err.message).toBe("HTTP 500");
  });

  it.each([502, 503, 504])("%i mit Grund vom Server: dessen Text", async (code) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Datenbank wird gesichert." }), { status: code })));

    const err = (await useAuthStore.getState().refresh().catch((e: unknown) => e)) as ServerUnavailableError;

    expect(err.status).toBe(code);
    expect(err.message).toBe("Datenbank wird gesichert.");
  });

  it.each([502, 503, 504])("%i mit HTML vom Proxy: unverändert \"nicht erreichbar\", ohne Status", async (code) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad Gateway</html>", { status: code })));

    const err = (await useAuthStore.getState().refresh().catch((e: unknown) => e)) as ServerUnavailableError;

    expect(err).toBeInstanceOf(ServerUnavailableError);
    expect(err.status).toBeUndefined();
    expect(err.message).toBe(SERVER_UNAVAILABLE_TEXT);
  });

  it("Netzwerkfehler: Anmeldung bleibt, klare deutsche Meldung", async () => {
    signedIn();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

    await expect(useAuthStore.getState().refresh()).rejects.toThrow("Server gerade nicht erreichbar, bitte gleich noch einmal versuchen.");
    expect(SERVER_UNAVAILABLE_TEXT).toBe("Server gerade nicht erreichbar, bitte gleich noch einmal versuchen.");
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  it("Erfolg mit unlesbarer Antwort (z. B. HTML vom Proxy): Anmeldung bleibt", async () => {
    signedIn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Wartung</html>", { status: 200 })));

    await expect(useAuthStore.getState().refresh()).rejects.toBeInstanceOf(ServerUnavailableError);
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().accessToken).toBe("tok-alt");
  });

  it("beim ersten Laden (unknown) bleibt der Status unknown -- kein Abmelden", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));

    await expect(useAuthStore.getState().refresh()).rejects.toBeInstanceOf(ServerUnavailableError);
    expect(useAuthStore.getState().status).toBe("unknown");
  });

  it("späterer Versuch klappt: neues Token, weiter angemeldet", async () => {
    signedIn();
    const answers: Array<() => Response> = [
      () => new Response("", { status: 502 }),
      () => new Response(JSON.stringify(SUCCESS_BODY), { status: 200 }),
    ];
    vi.stubGlobal("fetch", vi.fn(async () => (answers.shift() as () => Response)()));

    await expect(useAuthStore.getState().refresh()).rejects.toBeInstanceOf(ServerUnavailableError);
    // Der gescheiterte Aufruf blockiert keinen neuen (In-Flight-Zustand ist zurückgesetzt).
    await expect(useAuthStore.getState().refresh()).resolves.toBe(true);
    expect(useAuthStore.getState().accessToken).toBe("tok-1");
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("parallele Aufrufer teilen sich auch den Fehler (nur EINE Anfrage)", async () => {
    signedIn();
    const fetchMock = vi.fn(async () => new Response("", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    const results = await Promise.allSettled([useAuthStore.getState().refresh(), useAuthStore.getState().refresh()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(results.map((r) => r.status)).toEqual(["rejected", "rejected"]);
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  describe("Server antwortet gar nicht (Verbindung steht, aber keine Antwort)", () => {
    /** Wie ein echtes fetch: hängt, bis das Signal abbricht. */
    function stubHangingFetch() {
      const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Abgebrochen", "AbortError")));
      }));
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("nach dem Zeitlimit: ServerUnavailableError, Anmeldung bleibt, In-Flight-Zustand frei", async () => {
      signedIn();
      const fetchMock = stubHangingFetch();

      let settled = false;
      const outcome = useAuthStore.getState().refresh().then(() => "erneuert", (err: unknown) => err).finally(() => { settled = true; });
      await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS - 1);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      // Noch nicht abgelaufen: das Promise hängt weiter.
      expect(settled).toBe(false);

      await vi.advanceTimersByTimeAsync(1);
      expect(await outcome).toBeInstanceOf(ServerUnavailableError);
      expect(useAuthStore.getState().status).toBe("authenticated");
      expect(useAuthStore.getState().accessToken).toBe("tok-alt");

      // Ein neuer Aufruf startet eine neue Anfrage (das hängende Promise blockiert nicht mehr).
      void useAuthStore.getState().refresh().catch(() => {});
      expect(fetchMock).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
    });

    it("alle gleichzeitigen Aufrufer werden freigegeben", async () => {
      signedIn();
      const fetchMock = stubHangingFetch();

      const results = Promise.allSettled([useAuthStore.getState().refresh(), useAuthStore.getState().refresh()]);
      await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);

      expect((await results).map((r) => r.status)).toEqual(["rejected", "rejected"]);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("bei rechtzeitiger Antwort bleibt kein Timer zurück", async () => {
      signedIn();
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUCCESS_BODY), { status: 200 })));

      await expect(useAuthStore.getState().refresh()).resolves.toBe(true);

      expect(vi.getTimerCount()).toBe(0);
    });
  });

  it.each([400, 401, 403])("HTTP %i: Cookie abgelehnt -> abgemeldet", async (code) => {
    signedIn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Ungültig." }), { status: code })));

    await expect(useAuthStore.getState().refresh()).resolves.toBe(false);

    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
  });
});

describe("Fehlertexte bei abgelehnter Anmeldung", () => {
  function stubFetch(res: Response) {
    vi.stubGlobal("fetch", vi.fn(async () => res));
  }

  it("429 beim Login: Hinweis des Servers", async () => {
    stubFetch(new Response(JSON.stringify({ detail: "Zu viele Fehlversuche. Bitte in 3 Minuten erneut versuchen." }), { status: 429, headers: { "Retry-After": "170" } }));
    const result = await useAuthStore.getState().login("nico", "x");
    expect(result).toEqual({ ok: false, error: "Zu viele Fehlversuche. Bitte in 3 Minuten erneut versuchen." });
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("429 ohne JSON (z. B. von einem Proxy): Wartezeit aus Retry-After", async () => {
    stubFetch(new Response("Too Many Requests", { status: 429, headers: { "Retry-After": "240" } }));
    expect(await useAuthStore.getState().login("nico", "x")).toEqual({ ok: false, error: "Zu viele Fehlversuche. Bitte in 4 Minuten erneut versuchen." });

    stubFetch(new Response("", { status: 429 }));
    expect(await useAuthStore.getState().login("nico", "x")).toEqual({ ok: false, error: "Zu viele Fehlversuche. Bitte später erneut versuchen." });
  });

  it("429 beim zweiten Faktor: Hinweis, zurück zum Passwort-Schritt", async () => {
    useAuthStore.setState({ mfaToken: "mfa" });
    stubFetch(new Response(JSON.stringify({ detail: "Zu viele falsche Zwei-Faktor-Codes. Bitte melde dich erneut an." }), { status: 429 }));
    const result = await useAuthStore.getState().submitMfa("123456");
    expect(result).toEqual({ ok: false, error: "Zu viele falsche Zwei-Faktor-Codes. Bitte melde dich erneut an." });
    expect(useAuthStore.getState().mfaToken).toBeNull();
  });

  it("falscher Code (401): bleibt beim zweiten Faktor", async () => {
    useAuthStore.setState({ mfaToken: "mfa" });
    stubFetch(new Response(JSON.stringify({ detail: "Ungültiger Zwei-Faktor-Code." }), { status: 401 }));
    expect(await useAuthStore.getState().submitMfa("123456")).toEqual({ ok: false, error: "Ungültiger Zwei-Faktor-Code." });
    expect(useAuthStore.getState().mfaToken).toBe("mfa");
  });

  it("422 mit detail-Liste: ein lesbarer Satz, kein Objekt und kein „HTTP 422“", async () => {
    useAuthStore.setState({ mfaToken: "mfa" });
    stubFetch(new Response(JSON.stringify({ detail: [{ type: "string_too_short", loc: ["body", "code"], msg: "Mindestens 6 Zeichen." }] }), { status: 422 }));
    expect(await useAuthStore.getState().submitMfa("12345")).toEqual({ ok: false, error: "Code: Mindestens 6 Zeichen." });
  });
});

/** Zu kurzes Passwort oder Benutzername im Assistenten zeigten nur „HTTP 422“. */
describe("useAuthStore.bootstrap() -- Eingabefehler des Servers (422)", () => {
  function stubFetch(res: Response) {
    vi.stubGlobal("fetch", vi.fn(async () => res));
  }

  function reject422(detail: unknown[]) {
    stubFetch(new Response(JSON.stringify({ detail }), { status: 422 }));
  }

  it("zu kurzes Passwort: „Passwort: Mindestens 8 Zeichen.“", async () => {
    reject422([{ type: "string_too_short", loc: ["body", "password"], msg: "Mindestens 8 Zeichen." }]);
    expect(await useAuthStore.getState().bootstrap("nico", "kurz", "CODE")).toEqual({ ok: false, error: "Passwort: Mindestens 8 Zeichen." });
  });

  it("zu kurzer Benutzername: „Benutzername: Mindestens 3 Zeichen.“", async () => {
    reject422([{ type: "string_too_short", loc: ["body", "username"], msg: "Mindestens 3 Zeichen." }]);
    expect(await useAuthStore.getState().bootstrap("ko", "correct-horse-battery", "CODE")).toEqual({ ok: false, error: "Benutzername: Mindestens 3 Zeichen." });
  });

  it("Benutzername mit Leerzeichen: der Satz des Servers steht unverändert da (nennt sein Feld schon selbst)", async () => {
    const msg = "Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt, ohne Leerzeichen; er muss mit einem Buchstaben oder einer Ziffer beginnen.";
    reject422([{ type: "value_error", loc: ["body", "username"], msg }]);
    expect(await useAuthStore.getState().bootstrap("kollege max", "correct-horse-battery", "CODE")).toEqual({ ok: false, error: msg });
  });

  it("mehrere Fehler werden zusammengefasst, unbekannte Felder behalten ihren Namen", async () => {
    reject422([
      { type: "string_too_short", loc: ["body", "username"], msg: "Mindestens 3 Zeichen." },
      { type: "string_too_short", loc: ["body", "password"], msg: "Mindestens 8 Zeichen." },
      { type: "missing", loc: ["body", "irgendwas"], msg: "Pflichtangabe fehlt." },
    ]);
    expect(await useAuthStore.getState().bootstrap("a", "b", "CODE")).toEqual({
      ok: false,
      error: "Benutzername: Mindestens 3 Zeichen. Passwort: Mindestens 8 Zeichen. irgendwas: Pflichtangabe fehlt.",
    });
  });

  it("ohne lesbare Liste bleibt es bei „HTTP <Status>“", async () => {
    stubFetch(new Response("", { status: 422 }));
    expect(await useAuthStore.getState().bootstrap("nico", "kurz", "CODE")).toEqual({ ok: false, error: "HTTP 422" });
  });
});

describe("useAuthStore.refresh() -- überholte Antworten", () => {
  /** Hält die Antwort auf /auth/refresh zurück, bis der Test sie freigibt. */
  function stubHeldRefresh(answer: () => Response) {
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/refresh")) {
          await held;
          return answer();
        }
        if (url.endsWith("/auth/login")) return new Response(JSON.stringify({ ...SUCCESS_BODY, access_token: "tok-neu" }));
        return new Response("{}");
      }),
    );
    return () => release();
  }

  it("ein vor der Abmeldung gestarteter Refresh meldet danach nicht wieder an", async () => {
    useAuthStore.setState({ accessToken: "tok-0", user: SUCCESS_BODY.user, status: "authenticated" });
    const release = stubHeldRefresh(() => new Response(JSON.stringify(SUCCESS_BODY)));
    const pending = useAuthStore.getState().refresh();
    await useAuthStore.getState().logout();
    release();

    expect(await pending).toBe(false);
    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.accessToken).toBeNull();
  });

  it("eine verspätete Ablehnung meldet ein inzwischen angemeldetes Konto nicht ab", async () => {
    const release = stubHeldRefresh(() => new Response("{}", { status: 401 }));
    const pending = useAuthStore.getState().refresh();
    expect((await useAuthStore.getState().login("nico", "geheim-123")).ok).toBe(true);
    release();

    expect(await pending).toBe(true);
    const state = useAuthStore.getState();
    expect(state.status).toBe("authenticated");
    expect(state.accessToken).toBe("tok-neu");
  });
});

describe("hasPermission", () => {
  it("ein eingeschränktes Recht verdeckt kein späteres globales, egal in welcher Reihenfolge", () => {
    const user = { ...SUCCESS_BODY.user, is_owner: false };
    for (const permissions of [["hosts.read:h1", "hosts.read"], ["hosts.read", "hosts.read:h1"]]) {
      useAuthStore.setState({ user: { ...user, permissions } });
      expect(useAuthStore.getState().hasPermission("hosts.read")).toBe(true);
      expect(useAuthStore.getState().hasPermission("hosts.read:h2")).toBe(true);
    }
    useAuthStore.setState({ user: { ...user, permissions: ["hosts.read:h1"] } });
    expect(useAuthStore.getState().hasPermission("hosts.read")).toBe(false);
    expect(useAuthStore.getState().hasPermission("hosts.read:h1")).toBe(true);
  });
});
