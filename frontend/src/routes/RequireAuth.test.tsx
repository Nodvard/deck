import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { LoginPage } from "./LoginPage";
import { RequireAuth } from "./RequireAuth";

const USER = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] };
const { login: realLogin, submitMfa: realSubmitMfa } = useAuthStore.getState();

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search + location.hash}</p>;
}

/** Wie router.tsx: /login oeffentlich, alles andere hinter RequireAuth. */
function renderApp(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route path="*" element={<Where />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function stubServer({ mfa }: { mfa: boolean }) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
    if (url === "/api/v1/auth/login" && mfa) return new Response(JSON.stringify({ mfa_token: "mfa" }), { status: 202 });
    if (url === "/api/v1/auth/login" || url === "/api/v1/auth/mfa") {
      return new Response(JSON.stringify({ access_token: "tok", expires_in: 900, user: USER }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
}

function signIn() {
  fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "nico" } });
  fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "geheim" } });
  fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: null, login: realLogin, submitMfa: realSubmitMfa });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RequireAuth -> Anmeldung -> zurück zur Ziel-Adresse", () => {
  it("ntfy-Link mit ?tab=&host=: nach dem Login derselbe Reiter", async () => {
    stubServer({ mfa: false });
    renderApp("/ext/nexus-soc/soc?tab=guard&host=h-pi#oben");
    signIn();
    expect((await screen.findByTestId("where")).textContent).toBe("/ext/nexus-soc/soc?tab=guard&host=h-pi#oben");
  });

  it("auch mit zweitem Faktor", async () => {
    stubServer({ mfa: true });
    renderApp("/ext/nexus-soc/soc?tab=quarantine");
    signIn();
    fireEvent.change(await screen.findByLabelText("Sechsstelliger Code"), { target: { value: "123 456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    expect((await screen.findByTestId("where")).textContent).toBe("/ext/nexus-soc/soc?tab=quarantine");
  });

  it("direkt /login aufgerufen: danach die Startseite", async () => {
    stubServer({ mfa: false });
    renderApp("/login");
    signIn();
    expect((await screen.findByTestId("where")).textContent).toBe("/");
  });
});

/**
 * Seiten wie Nodvard Shield schreiben Reiter und Filter per replaceState in die Adresszeile,
 * am Router vorbei. Nach abgelaufener Sitzung muss die Anmeldung dorthin zurueckfuehren,
 * wo man wirklich war -- nicht auf die (aeltere) Adresse der letzten Router-Navigation.
 */
describe("RequireAuth -- Rückkehradresse aus der Adresszeile", () => {
  function FromProbe() {
    const location = useLocation();
    return <p data-testid="from">{(location.state as { from?: string } | null)?.from ?? ""}</p>;
  }

  afterEach(() => window.history.replaceState(null, "", "/"));

  it("nach Sitzungsablauf: Reiter und Filter aus der Adresszeile bleiben erhalten", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc?tab=updates#oben");
    useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
    render(
      <MemoryRouter initialEntries={["/ext/nexus-soc/soc"]}>
        <Routes>
          <Route path="/login" element={<FromProbe />} />
          <Route element={<RequireAuth />}>
            <Route path="*" element={<Where />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("where")).toBeInTheDocument();

    act(() => useAuthStore.setState({ accessToken: null, user: null, status: "anonymous" }));

    expect(screen.getByTestId("from").textContent).toBe("/ext/nexus-soc/soc?tab=updates#oben");
  });
});

/**
 * Erster Aufruf der Seite (Reload, ntfy-Link), waehrend die API nicht antwortet (index.html
 * ist da, z. B. aus dem Cache; laeuft der Container gar nicht, zeigt der Browser seine
 * eigene Fehlerseite): der stille Refresh scheitert an 502/503/504 oder einer abgelehnten
 * Verbindung. Das sagt nichts ueber das Cookie -- nicht auf den Login werfen, sondern
 * warten und es von selbst noch einmal versuchen.
 */
describe("RequireAuth -- Server beim ersten Laden nicht erreichbar", () => {
  let serverUp: boolean;
  let refreshCalls: number;

  function stubRefresh(down: () => Response | Promise<Response>, cookieValid = true) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) !== "/api/v1/auth/refresh") throw new Error(`Unerwarteter Fetch: ${String(input)}`);
      refreshCalls += 1;
      if (!serverUp) return down();
      if (!cookieValid) return new Response(JSON.stringify({ detail: "Ungültig." }), { status: 401 });
      return new Response(JSON.stringify({ access_token: "tok", expires_in: 900, user: USER }), { status: 200 });
    }));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    serverUp = false;
    refreshCalls = 0;
    useAuthStore.setState({ accessToken: null, user: null, status: "unknown", mfaToken: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function tick(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it("503: kein Login, Hinweis, nach 5 s neuer Versuch -> Seite erscheint", async () => {
    stubRefresh(() => new Response("Service Unavailable", { status: 503 }));
    renderApp("/ext/nexus-soc/soc?tab=guard");
    await tick(0);

    expect(refreshCalls).toBe(1);
    expect(screen.getByRole("status")).toHaveTextContent("Server nicht erreichbar – neuer Versuch …");
    expect(screen.queryByLabelText("Benutzername")).not.toBeInTheDocument();
    expect(screen.queryByTestId("where")).not.toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe("unknown");

    serverUp = true;
    await tick(4_999);
    expect(refreshCalls).toBe(1);
    await tick(1);
    expect(refreshCalls).toBe(2);

    // Dieselbe Adresse wie angefordert, kein Umweg ueber den Login.
    expect(screen.getByTestId("where").textContent).toBe("/ext/nexus-soc/soc?tab=guard");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("507 mit Grund (Platte voll): der Text des Servers, kein Login, es wird weiter versucht", async () => {
    const detail = "Der Speicherplatz auf dem Server ist voll. Bitte Platz schaffen.";
    stubRefresh(() => new Response(JSON.stringify({ detail }), { status: 507 }));
    renderApp("/ext/nexus-soc/soc?tab=guard");
    await tick(0);

    expect(refreshCalls).toBe(1);
    expect(screen.getByRole("status")).toHaveTextContent(`Server meldet: ${detail}`);
    expect(screen.getByRole("status")).not.toHaveTextContent("nicht erreichbar");
    expect(screen.getByRole("link", { name: "Zur Anmeldung" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jetzt erneut versuchen" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Benutzername")).not.toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe("unknown");

    serverUp = true;
    await tick(5_000);
    expect(refreshCalls).toBe(2);
    expect(screen.getByTestId("where").textContent).toBe("/ext/nexus-soc/soc?tab=guard");
  });

  it("502 mit Fehlerseite vom Proxy: weiter der feste Text „Server nicht erreichbar“", async () => {
    stubRefresh(() => new Response("<html>Bad Gateway</html>", { status: 502 }));
    renderApp("/");
    await tick(0);

    expect(screen.getByRole("status")).toHaveTextContent("Server nicht erreichbar – neuer Versuch …");
    expect(screen.getByRole("status")).not.toHaveTextContent("Server meldet");
  });

  it("Netzwerkfehler: dasselbe, Pausen 5 s, 15 s, 30 s, dann jede Minute", async () => {
    stubRefresh(() => { throw new TypeError("Failed to fetch"); });
    renderApp("/");
    await tick(0);
    expect(refreshCalls).toBe(1);

    for (const [wait, calls] of [[5_000, 2], [15_000, 3], [30_000, 4], [60_000, 5], [60_000, 6]] as const) {
      await tick(wait - 1);
      expect(refreshCalls).toBe(calls - 1);
      await tick(1);
      expect(refreshCalls).toBe(calls);
      expect(screen.getByRole("status")).toHaveTextContent("Server nicht erreichbar");
      expect(screen.queryByLabelText("Benutzername")).not.toBeInTheDocument();
    }

    serverUp = true;
    await tick(60_000);
    expect(screen.getByTestId("where").textContent).toBe("/");
  });

  it("Knopf „Jetzt erneut versuchen“: sofort ein neuer Versuch, danach wieder der Takt ab 5 s", async () => {
    stubRefresh(() => new Response("Not Found", { status: 404 }));
    renderApp("/");
    await tick(0);
    await tick(5_000);
    await tick(15_000);
    expect(refreshCalls).toBe(3);

    serverUp = true;
    fireEvent.click(screen.getByRole("button", { name: "Jetzt erneut versuchen" }));
    await tick(0);

    // Kein Warten auf den naechsten Takt: sofort erneuert, Seite erscheint.
    expect(refreshCalls).toBe(4);
    expect(screen.getByTestId("where").textContent).toBe("/");
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("Knopf, Server immer noch weg: Hinweis bleibt, der Takt beginnt wieder bei 5 s", async () => {
    stubRefresh(() => new Response("", { status: 503 }));
    renderApp("/");
    await tick(0);
    await tick(5_000);
    await tick(15_000);
    expect(refreshCalls).toBe(3);

    fireEvent.click(screen.getByRole("button", { name: "Jetzt erneut versuchen" }));
    await tick(0);
    expect(refreshCalls).toBe(4);
    expect(screen.getByRole("status")).toHaveTextContent("Server nicht erreichbar");

    await tick(4_999);
    expect(refreshCalls).toBe(4);
    await tick(1);
    expect(refreshCalls).toBe(5);
  });

  it("Link „Zur Anmeldung“: Anmeldeseite, keine weiteren Versuche, danach zurück zur Ziel-Adresse", async () => {
    let refreshAttempts = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/auth/refresh") {
        refreshAttempts += 1;
        return new Response("Bad Gateway", { status: 502 });
      }
      if (url === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
      if (url === "/api/v1/auth/login") return new Response(JSON.stringify({ access_token: "tok", expires_in: 900, user: USER }), { status: 200 });
      throw new Error(`Unerwarteter Fetch: ${url}`);
    }));
    renderApp("/ext/nexus-soc/soc?tab=guard");
    await tick(0);
    expect(screen.getByRole("link", { name: "Zur Anmeldung" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Zur Anmeldung" }));
    await tick(0);
    expect(screen.getByLabelText("Benutzername")).toBeInTheDocument();
    const attemptsAtLogin = refreshAttempts;
    await tick(5 * 60_000);
    expect(refreshAttempts).toBe(attemptsAtLogin);

    signIn();
    await tick(0);
    expect(screen.getByTestId("where").textContent).toBe("/ext/nexus-soc/soc?tab=guard");
  });

  it("Server erreichbar, Cookie abgelehnt (401): Weiterleitung zum Login", async () => {
    serverUp = true;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
      return new Response(JSON.stringify({ detail: "Ungültig." }), { status: 401 });
    }));
    renderApp("/ext/nexus-soc/soc");
    await tick(0);

    expect(screen.getByLabelText("Benutzername")).toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });

  it("Seite verlassen: keine weiteren Versuche", async () => {
    stubRefresh(() => new Response("", { status: 502 }));
    const { unmount } = renderApp("/");
    await tick(0);
    expect(refreshCalls).toBe(1);

    unmount();
    await tick(5 * 60_000);
    expect(refreshCalls).toBe(1);
  });
});
