import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { browserNavigation, restartProbe, restartTiming, uploadTransport } from "../lib/restore";
import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";
import { SetupPage } from "./SetupPage";

const DEFAULT_BRANDING = {
  product_name: "Nodvard Deck",
  short_name: "Nodvard Deck",
  logo_url: null,
  favicon_url: null,
  login_subtitle: null,
  support_url: null,
  colors: { accent: "#0ea5e9", accent_strong: "#0369a1", background: "#0b1220", surface: "#111a2b", text: "#e6ebf5" },
};

const EXTENSIONS = [
  { id: "proxmox", state: "disabled", name: "Proxmox VE", description: "Liest deine Proxmox-Server ein. Mit z. B. Snapshots.", icon: "server", last_error: null, category: "servers", sort_order: 40 },
  { id: "system", state: "disabled", name: "System", description: "Zustand eines Servers auf einer Seite: Auslastung, Speicher.", icon: "cpu", last_error: null, category: "servers", sort_order: 10 },
  { id: "terminal", state: "disabled", name: "Terminal", description: "Web-Terminal per SSH.", icon: "terminal", last_error: null, category: "servers", sort_order: 20 },
  { id: "ntfy", state: "disabled", name: "ntfy-Benachrichtigungen", description: "Schickt Meldungen aufs Handy.", icon: "bell", last_error: null, category: "connections", sort_order: 10 },
  { id: "shield", state: "disabled", name: "Nodvard Shield", description: "Virenschutz für alle Server.", icon: "shield-alert", last_error: null, category: "security", sort_order: 10 },
  { id: "fremd", state: "disabled", name: "Fremdmodul", description: null, icon: null, last_error: null },
];

/** Ein kleines Attrappen-Backend: merkt sich Zustand und Aufrufe, wie es der echte Server täte. */
function makeBackend(opts: { bootstrapNeeded?: boolean; refreshOk?: boolean; timezone?: string; totp?: boolean } = {}) {
  const state = {
    bootstrapNeeded: opts.bootstrapNeeded ?? true,
    refreshOk: opts.refreshOk ?? false,
    timezone: opts.timezone ?? "Europe/Berlin",
    totp: opts.totp ?? false,
    extensions: structuredClone(EXTENSIONS) as Record<string, unknown>[],
    /** Namen von Zonen, die der Server nicht kennt (PUT -> 422). */
    unknownZones: new Set<string>(),
    /** Module, die sich nicht einschalten lassen (Server meldet state "error"). */
    brokenModules: new Set<string>(),
  };
  const calls: { method: string; path: string; body: unknown }[] = [];
  const user = { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] };
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const path = url.replace(/^\/api\/v1/, "").split("?")[0];
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });

    if (path === "/auth/bootstrap" && method === "GET") return json({ needed: state.bootstrapNeeded });
    if (path === "/auth/bootstrap" && method === "POST") {
      state.bootstrapNeeded = false;
      return json({ id: "u1", username: "nico", is_owner: true }, 201);
    }
    if (path === "/auth/login") return json({ access_token: "tok", user });
    if (path === "/auth/refresh") return state.refreshOk ? json({ access_token: "tok2", user }) : json({ detail: "abgelaufen" }, 401);
    if (path === "/branding" && method === "GET") return json(DEFAULT_BRANDING);
    if (path === "/branding" && method === "PUT") return json({ ...DEFAULT_BRANDING, ...body });
    if (path === "/me") return json({ ...user, totp_enabled: state.totp, timezone: state.timezone });
    if (path === "/settings/system.timezone" && method === "PUT") {
      const zone = (body as { value: string }).value;
      if (state.unknownZones.has(zone)) return json({ detail: `system.timezone: unbekannte Zeitzone '${zone}'` }, 422);
      state.timezone = zone;
      return json({ key: "system.timezone", value: zone });
    }
    if (path === "/extensions" && method === "GET") {
      return json(state.extensions.map((e) => ({ ...e, needs_setup: e.state === "enabled" && ["ntfy", "proxmox"].includes(e.id as string) })));
    }
    const toggle = path.match(/^\/extensions\/([^/]+)\/(enable|disable)$/);
    if (toggle && method === "POST") {
      const ext = state.extensions.find((e) => e.id === toggle[1])!;
      ext.state = toggle[2] === "disable" ? "disabled" : state.brokenModules.has(ext.id as string) ? "error" : "enabled";
      if (ext.state === "error") ext.last_error = "on_start() fehlgeschlagen: kein Zugriff";
      return json(ext);
    }
    if (path === "/me/totp/setup" && method === "POST") {
      return json({ secret: "ABCDEFGHIJKLMNOP", otpauth_uri: "otpauth://totp/Nodvard%20Deck:nico?secret=ABCDEFGHIJKLMNOP&issuer=Nodvard%20Deck" });
    }
    if (path === "/me/totp/confirm" && method === "POST") {
      state.totp = true;
      return json({ recovery_codes: ["AAAAA-BBBBB", "CCCCC-DDDDD"] });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, calls, fetchMock, count: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path).length };
}

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={["/setup"]}>
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function deviceZone(zone: string) {
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({ timeZone: zone } as Intl.ResolvedDateTimeFormatOptions);
}

async function fillAccount(code = "ABCD-EFGH-JKMN") {
  await screen.findByText("Willkommen");
  fireEvent.change(screen.getByLabelText(/^Einrichtungscode/), { target: { value: code } });
  fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "nico" } });
  fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "correct-horse-battery" } });
  fireEvent.change(screen.getByLabelText("Passwort bestätigen"), { target: { value: "correct-horse-battery" } });
}

async function createAccount() {
  await fillAccount();
  fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));
  await screen.findByText(/Schritt 2 von 6/);
}

/** Schrittanzeige „Schritt X von 6 — Titel“. */
function progress() {
  return screen.getByTestId("setup-progress").textContent;
}

async function toModules() {
  await createAccount();
  await screen.findByTestId("setup-timezone");
  fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
  await screen.findByTestId("setup-modules");
  await screen.findByTestId("module-system");
}

async function toTotp() {
  await toModules();
  fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
  await screen.findByTestId("setup-totp");
  await screen.findByRole("button", { name: "Jetzt einrichten" });
}

/** Nach „Jetzt einrichten“ verlangt der Server das aktuelle Passwort. */
async function enterTotpPassword(password = "geheim123") {
  fireEvent.change(await screen.findByLabelText(/^Passwort zur Bestätigung/), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
}

async function toBranding() {
  await toTotp();
  fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
  await screen.findByText(/Schritt 5 von 6/);
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: null, user: null, status: "unknown", mfaToken: null });
  useBrandingStore.setState({ branding: null, loaded: false });
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("SetupPage", () => {
  it("leitet auf /login um, wenn bereits ein Nutzer existiert", async () => {
    makeBackend({ bootstrapNeeded: false });
    renderSetup();
    await waitFor(() => expect(screen.queryByText(/Lade/)).not.toBeInTheDocument());
    expect(screen.queryByText("Willkommen")).not.toBeInTheDocument();
    expect(screen.getByTestId("where").textContent).toBe("/login");
  });

  it("legt den Owner an, loggt ein und geht zu Schritt 2 (Zeitzone)", async () => {
    makeBackend();
    renderSetup();
    await screen.findByText("Willkommen");
    expect(progress()).toBe("Schritt 1 von 6 — Administrator-Konto anlegen");
    await createAccount();
    expect(progress()).toBe("Schritt 2 von 6 — Zeitzone");
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("Server nicht erreichbar beim Anlegen: Hinweis, Knopf wieder frei", async () => {
    const backend = makeBackend();
    const online = backend.fetchMock.getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/v1/auth/bootstrap") && init?.method === "POST") throw new TypeError("Failed to fetch");
      return online(input, init);
    }));
    renderSetup();
    await fillAccount();
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));

    expect(await screen.findByText("Server nicht erreichbar – bitte gleich noch einmal versuchen.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Konto anlegen" })).toBeEnabled();
  });

  it("verlangt den Einrichtungscode und schickt ihn mit", async () => {
    const backend = makeBackend();
    renderSetup();

    await screen.findByText("Willkommen");
    expect(screen.getByText(/docker compose logs nodvard-deck/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "nico" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText("Passwort bestätigen"), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));
    expect(await screen.findByText("Bitte den Einrichtungscode eingeben.")).toBeInTheDocument();
    expect(backend.count("POST", "/auth/bootstrap")).toBe(0);

    fireEvent.change(screen.getByLabelText(/^Einrichtungscode/), { target: { value: "abcd-efgh-jkmn" } });
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));
    await screen.findByText(/Schritt 2 von 6/);
    expect(backend.calls.find((c) => c.method === "POST" && c.path === "/auth/bootstrap")!.body).toMatchObject({ username: "nico", setup_code: "ABCD-EFGH-JKMN" });
  });

  it("Passwortfelder sind „neues Passwort“ (Browser schlagen kein altes vor), Fehler werden vorgelesen", async () => {
    makeBackend();
    renderSetup();
    await screen.findByText("Willkommen");
    expect(screen.getByLabelText("Passwort")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Passwort bestätigen")).toHaveAttribute("autocomplete", "new-password");
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "nico" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText("Passwort bestätigen"), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte den Einrichtungscode eingeben.");
  });

  it("zeigt die Meldung des Servers bei falschem Code", async () => {
    const backend = makeBackend();
    const online = backend.fetchMock.getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/v1/auth/bootstrap") && init?.method === "POST") {
        return new Response(JSON.stringify({ detail: "Der Einrichtungscode stimmt nicht." }), { status: 403 });
      }
      return online(input, init);
    }));
    renderSetup();
    await fillAccount("AAAA-AAAA-AAAA");
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));
    expect(await screen.findByText("Der Einrichtungscode stimmt nicht.")).toBeInTheDocument();
    expect(progress()).toMatch(/Schritt 1 von 6/);
  });

  it("zeigt einen Fehler, wenn die Passwoerter nicht uebereinstimmen, ohne zu fetchen", async () => {
    const backend = makeBackend();
    renderSetup();
    await fillAccount();
    fireEvent.change(screen.getByLabelText("Passwort bestätigen"), { target: { value: "anders" } });
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));

    expect(await screen.findByText("Passwörter stimmen nicht überein.")).toBeInTheDocument();
    expect(backend.count("POST", "/auth/bootstrap")).toBe(0);
  });

  it("sagt unter den Feldern, was erlaubt ist: Zeichen im Benutzernamen, mindestens 8 Zeichen beim Passwort", async () => {
    makeBackend();
    renderSetup();
    await screen.findByText("Willkommen");
    expect(screen.getByText(/Nur Kleinbuchstaben, Ziffern sowie \. - und _, mindestens 3 Zeichen, ohne Leerzeichen/)).toBeInTheDocument();
    expect(screen.getByText("Mindestens 8 Zeichen.")).toBeInTheDocument();
    // Der Hinweis hängt am Feld, ohne dessen Namen zu verändern.
    expect(screen.getByLabelText("Passwort")).toHaveAccessibleDescription("Mindestens 8 Zeichen.");
  });

  it.each([
    ["zu kurzes Passwort", { user: "nico", pw: "kurz" }, "Passwort: Mindestens 8 Zeichen."],
    ["zu kurzer Benutzername", { user: "ko", pw: "correct-horse-battery" }, "Benutzername: Mindestens 3 Zeichen."],
    ["Benutzername mit Leerzeichen", { user: "kollege max", pw: "correct-horse-battery" }, /^Benutzername: Nur Kleinbuchstaben, Ziffern sowie \. - und _ erlaubt, ohne Leerzeichen/],
  ])("%s: klare Meldung statt „HTTP 422“, ohne etwas zu senden", async (_name, input, expected) => {
    const backend = makeBackend();
    renderSetup();
    await fillAccount();
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: input.user } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: input.pw } });
    fireEvent.change(screen.getByLabelText("Passwort bestätigen"), { target: { value: input.pw } });
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 422/)).not.toBeInTheDocument();
    expect(backend.count("POST", "/auth/bootstrap")).toBe(0);
    expect(progress()).toMatch(/Schritt 1 von 6/);
  });

  it("lehnt der Server trotzdem mit 422 ab, steht dessen deutscher Satz da (nicht „HTTP 422“)", async () => {
    const backend = makeBackend();
    const online = backend.fetchMock.getMockImplementation()!;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/v1/auth/bootstrap") && init?.method === "POST") {
        return new Response(JSON.stringify({ detail: [{ type: "string_too_short", loc: ["body", "password"], msg: "Mindestens 8 Zeichen." }] }), { status: 422 });
      }
      return online(input, init);
    }));
    renderSetup();
    await fillAccount();
    fireEvent.click(screen.getByRole("button", { name: "Konto anlegen" }));
    expect(await screen.findByText("Passwort: Mindestens 8 Zeichen.")).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 422/)).not.toBeInTheDocument();
    expect(progress()).toMatch(/Schritt 1 von 6/);
  });

  it("klappt die Anleitung zum Einrichtungscode auf: Befehlszeile, Docker Desktop, Portainer, Synology, Unraid", async () => {
    makeBackend();
    renderSetup();
    await screen.findByText("Willkommen");
    const toggle = screen.getByRole("button", { name: "Wo finde ich den Code?" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Docker Desktop")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("docker compose logs nodvard-deck | grep -A1 Einrichtungscode")).toBeInTheDocument();
    for (const name of ["Befehlszeile", "Docker Desktop", "Portainer", "Synology Container Manager", "Unraid"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getByText(/Reiter „Logs“/)).toBeInTheDocument();
    expect(screen.getByText(/Reiter „Protokoll“/)).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText("Docker Desktop")).not.toBeInTheDocument();
  });

  describe("Schritt Zeitzone", () => {
    it("ist mit der Zone des Geräts vorbelegt und speichert sie über die Einstellungen", async () => {
      deviceZone("America/New_York");
      const backend = makeBackend({ timezone: "Europe/Berlin" });
      renderSetup();
      await createAccount();

      const list = await screen.findByRole("listbox", { name: "Zeitzonen" });
      expect(within(list).getByRole("option", { selected: true })).toHaveTextContent("America/New_York");
      expect(screen.getByText(/Vorausgewählt ist die Zeitzone dieses Geräts/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
      await screen.findByText(/Schritt 3 von 6/);
      const put = backend.calls.find((c) => c.method === "PUT" && c.path === "/settings/system.timezone");
      expect(put?.body).toEqual({ value: "America/New_York" });
    });

    it("sucht in der Liste wie in den Einstellungen und speichert die gewählte Zone", async () => {
      deviceZone("Europe/Berlin");
      const backend = makeBackend({ timezone: "UTC" });
      renderSetup();
      await createAccount();

      await screen.findByRole("listbox", { name: "Zeitzonen" });
      fireEvent.change(screen.getByLabelText("Zeitzone suchen"), { target: { value: "tokyo" } });
      fireEvent.click(within(screen.getByRole("listbox", { name: "Zeitzonen" })).getByRole("option", { name: /^Asia\/Tokyo/ }).querySelector("button")!);
      expect(screen.getByText("nicht gespeichert")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
      await screen.findByText(/Schritt 3 von 6/);
      expect(backend.calls.find((c) => c.path === "/settings/system.timezone")?.body).toEqual({ value: "Asia/Tokyo" });
    });

    it("ruft nichts auf, wenn die Zone des Geräts schon eingestellt ist", async () => {
      deviceZone("Europe/Berlin");
      const backend = makeBackend({ timezone: "Europe/Berlin" });
      renderSetup();
      await createAccount();
      await screen.findByRole("listbox", { name: "Zeitzonen" });
      expect(screen.queryByText(/Vorausgewählt ist/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
      await screen.findByText(/Schritt 3 von 6/);
      expect(backend.count("PUT", "/settings/system.timezone")).toBe(0);
    });

    it("nimmt statt der Gerätezone die jetzige Einstellung, wenn der Browser die Zone nicht kennt", async () => {
      deviceZone("Mars/Olympus");
      makeBackend({ timezone: "Europe/Vienna" });
      renderSetup();
      await createAccount();
      const list = await screen.findByRole("listbox", { name: "Zeitzonen" });
      expect(within(list).getByRole("option", { selected: true })).toHaveTextContent("Europe/Vienna");
    });

    it("sagt es, wenn der Server die Zone nicht kennt, und bleibt im Schritt", async () => {
      deviceZone("Asia/Tokyo");
      const backend = makeBackend({ timezone: "Europe/Berlin" });
      backend.state.unknownZones.add("Asia/Tokyo");
      renderSetup();
      await createAccount();
      await screen.findByRole("listbox", { name: "Zeitzonen" });
      fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
      expect(await screen.findByText("Diese Zeitzone kennt der Server nicht. Bitte eine andere aus der Liste wählen.")).toBeInTheDocument();
      expect(progress()).toMatch(/Schritt 2 von 6/);
      expect(within(screen.getByRole("listbox", { name: "Zeitzonen" })).getByRole("option", { selected: true })).toHaveTextContent("Europe/Berlin");
    });

    it("lässt sich überspringen, ohne zu speichern", async () => {
      deviceZone("America/New_York");
      const backend = makeBackend();
      renderSetup();
      await createAccount();
      await screen.findByRole("listbox", { name: "Zeitzonen" });
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      await screen.findByText(/Schritt 3 von 6/);
      expect(backend.count("PUT", "/settings/system.timezone")).toBe(0);
    });
  });

  describe("Schritt „Was willst du nutzen?“", () => {
    it("zeigt die Module als Kacheln: Server-nahe zuerst, nach Manifest gruppiert und geordnet", async () => {
      makeBackend();
      renderSetup();
      await toModules();

      expect(progress()).toBe("Schritt 3 von 6 — Was willst du nutzen?");
      const groups = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
      expect(groups).toEqual(["Für deine Server", "Sicherheit", "Verbindungen zu anderen Diensten", "Weitere Module"]);
      const tiles = screen.getAllByTestId(/^module-/).map((t) => t.getAttribute("data-testid"));
      expect(tiles).toEqual(["module-system", "module-terminal", "module-proxmox", "module-shield", "module-ntfy", "module-fremd"]);
      // Kurzbeschreibung = erster Satz ("z. B." trennt nicht), die volle Beschreibung steht im Tooltip.
      const proxmox = screen.getByTestId("module-proxmox");
      expect(within(proxmox).getByText("Liest deine Proxmox-Server ein.")).toHaveAttribute("title", "Liest deine Proxmox-Server ein. Mit z. B. Snapshots.");
      expect(screen.getByText("Zustand eines Servers auf einer Seite.")).toHaveAttribute("title", "Zustand eines Servers auf einer Seite: Auslastung, Speicher.");
    });

    it("schaltet ein Modul per Klick ein und wieder aus", async () => {
      const backend = makeBackend();
      renderSetup();
      await toModules();

      const toggle = within(screen.getByTestId("module-system")).getByRole("switch", { name: "System einschalten" });
      expect(toggle).toHaveAttribute("aria-checked", "false");
      fireEvent.click(toggle);
      await waitFor(() => expect(within(screen.getByTestId("module-system")).getByRole("switch", { name: "System ausschalten" })).toHaveAttribute("aria-checked", "true"));
      expect(backend.count("POST", "/extensions/system/enable")).toBe(1);
      expect(screen.getByTestId("modules-hint")).toHaveTextContent("1 Modul eingeschaltet.");

      fireEvent.click(within(screen.getByTestId("module-system")).getByRole("switch", { name: "System ausschalten" }));
      await waitFor(() => expect(within(screen.getByTestId("module-system")).getByRole("switch")).toHaveAttribute("aria-checked", "false"));
      expect(backend.count("POST", "/extensions/system/disable")).toBe(1);
    });

    it("schaltet auch per Klick auf die Kachel (nicht nur auf den Schalter)", async () => {
      const backend = makeBackend();
      renderSetup();
      await toModules();
      fireEvent.click(screen.getByText("Web-Terminal per SSH."));
      await waitFor(() => expect(backend.count("POST", "/extensions/terminal/enable")).toBe(1));
    });

    it("sagt bei Modulen, die noch Angaben brauchen: „Braucht danach noch Angaben“", async () => {
      makeBackend();
      renderSetup();
      await toModules();

      fireEvent.click(screen.getByRole("switch", { name: "ntfy-Benachrichtigungen einschalten" }));
      await screen.findByTestId("needs-setup-ntfy");
      expect(screen.getByTestId("needs-setup-ntfy")).toHaveTextContent("Braucht danach noch Angaben");
      // Ein Modul ohne offene Angaben bekommt den Hinweis nicht.
      fireEvent.click(screen.getByRole("switch", { name: "System einschalten" }));
      await waitFor(() => expect(screen.getByRole("switch", { name: "System ausschalten" })).toBeInTheDocument());
      expect(screen.queryByTestId("needs-setup-system")).not.toBeInTheDocument();
      expect(screen.getByTestId("modules-hint")).toHaveTextContent("2 Module eingeschaltet. 1 braucht danach noch Angaben.");
      expect(screen.getByTestId("modules-hint")).toHaveTextContent("Einstellungen → Erweiterungen");
    });

    it("meldet ein Modul, das beim Einschalten scheitert, und lässt es aus", async () => {
      const backend = makeBackend();
      backend.state.brokenModules.add("terminal");
      renderSetup();
      await toModules();
      fireEvent.click(screen.getByRole("switch", { name: "Terminal einschalten" }));
      expect(await screen.findByText(/„Terminal“ ließ sich nicht einschalten: on_start\(\) fehlgeschlagen/)).toBeInTheDocument();
      expect(screen.getByRole("switch", { name: "Terminal einschalten" })).toHaveAttribute("aria-checked", "false");
    });

    it("kein Modul ist Pflicht: ohne Auswahl heißt der Knopf „Überspringen“, mit Auswahl „Weiter“", async () => {
      makeBackend();
      renderSetup();
      await toModules();
      expect(screen.queryByRole("button", { name: "Weiter" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("switch", { name: "System einschalten" }));
      expect(await screen.findByRole("button", { name: "Weiter" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
      await screen.findByText(/Schritt 4 von 6/);
    });

    it("Zurück führt zur Zeitzone", async () => {
      makeBackend();
      renderSetup();
      await toModules();
      fireEvent.click(screen.getByRole("button", { name: "Zurück" }));
      await screen.findByTestId("setup-timezone");
      expect(progress()).toMatch(/Schritt 2 von 6/);
    });
  });

  describe("Schritt Zwei-Faktor", () => {
    it("zeigt den QR-Code und den Schlüssel, bestätigt den Code und zeigt dann die Wiederherstellungs-Codes", async () => {
      const backend = makeBackend();
      renderSetup();
      await toTotp();
      expect(progress()).toBe("Schritt 4 von 6 — Zwei-Faktor-Anmeldung (optional)");

      fireEvent.click(screen.getByRole("button", { name: "Jetzt einrichten" }));
      await enterTotpPassword();
      const qr = await screen.findByRole("img", { name: "QR-Code für die Authenticator-App" });
      expect(qr.querySelector("path")?.getAttribute("d")?.length ?? 0).toBeGreaterThan(200);
      expect(screen.getByTestId("totp-secret")).toHaveTextContent("ABCD EFGH IJKL MNOP");

      fireEvent.change(screen.getByLabelText("Bestätigungscode"), { target: { value: "123 456" } });
      fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
      const panel = await screen.findByTestId("recovery-codes");
      expect(panel).toHaveTextContent("AAAAA-BBBBB");
      expect(panel).toHaveTextContent("CCCCC-DDDDD");
      expect(backend.calls.find((c) => c.path === "/me/totp/confirm")?.body).toEqual({ code: "123456" });
      expect(backend.calls.find((c) => c.path === "/me/totp/setup")?.body).toEqual({ current_password: "geheim123" });
      // Erst nach „Ich habe die Codes gesichert“ geht es weiter.
      expect(progress()).toMatch(/Schritt 4 von 6/);
      fireEvent.click(screen.getByRole("button", { name: "Ich habe die Codes gesichert" }));
      await screen.findByText(/Schritt 5 von 6/);
    });

    it("zeigt den Fehler des Servers bei falschem Code und lässt es erneut versuchen", async () => {
      const backend = makeBackend();
      const online = backend.fetchMock.getMockImplementation()!;
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith("/me/totp/confirm")) return new Response(JSON.stringify({ detail: "Der Code stimmt nicht." }), { status: 400 });
        return online(input, init);
      }));
      renderSetup();
      await toTotp();
      fireEvent.click(screen.getByRole("button", { name: "Jetzt einrichten" }));
      await enterTotpPassword();
      await screen.findByTestId("totp-secret");
      fireEvent.change(screen.getByLabelText("Bestätigungscode"), { target: { value: "000000" } });
      fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
      expect(await screen.findByText("Der Code stimmt nicht.")).toBeInTheDocument();
      expect(screen.getByLabelText("Bestätigungscode")).toHaveValue("000000");
      expect(screen.queryByTestId("recovery-codes")).not.toBeInTheDocument();
    });

    it("lässt sich überspringen, ohne etwas einzurichten", async () => {
      const backend = makeBackend();
      renderSetup();
      await toBranding();
      expect(backend.count("POST", "/me/totp/setup")).toBe(0);
    });

    it("Abbrechen während der Einrichtung geht zurück zur Auswahl, Zurück zu den Modulen", async () => {
      makeBackend();
      renderSetup();
      await toTotp();
      fireEvent.click(screen.getByRole("button", { name: "Jetzt einrichten" }));
      await enterTotpPassword();
      await screen.findByTestId("totp-secret");
      fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
      expect(await screen.findByRole("button", { name: "Jetzt einrichten" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Zurück" }));
      await screen.findByTestId("setup-modules");
    });

    it("erkennt eine schon eingerichtete Zwei-Faktor-Anmeldung (z. B. nach dem Neuladen)", async () => {
      const backend = makeBackend({ totp: true });
      renderSetup();
      await toModules();
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      expect(await screen.findByText(/schon eingerichtet/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Jetzt einrichten" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
      await screen.findByText(/Schritt 5 von 6/);
      expect(backend.count("POST", "/me/totp/setup")).toBe(0);
    });
  });

  describe("Aussehen und Abschluss", () => {
    it("der Untertitel sagt, dass er auch der App-Name auf dem Handy ist", async () => {
      makeBackend();
      renderSetup();
      await toBranding();
      const field = screen.getByLabelText(/^Untertitel/);
      const hint = document.getElementById(field.getAttribute("aria-describedby") ?? "");
      expect(hint?.textContent).toContain("Name der App auf dem Handy");
    });

    it("Aussehen speichern führt zum Abschluss, Zurück zur Zwei-Faktor-Auswahl", async () => {
      const backend = makeBackend();
      renderSetup();
      await toBranding();
      fireEvent.change(screen.getByLabelText("Produktname"), { target: { value: "Mein Deck" } });
      fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
      await screen.findByText(/Schritt 6 von 6/);
      expect(backend.calls.find((c) => c.method === "PUT" && c.path === "/branding")?.body).toMatchObject({ product_name: "Mein Deck" });
      fireEvent.click(screen.getByRole("button", { name: "Zurück" }));
      await screen.findByText(/Schritt 5 von 6/);
      fireEvent.click(screen.getByRole("button", { name: "Zurück" }));
      await screen.findByText(/Schritt 4 von 6/);
    });

    it("Aussehen überspringen führt ebenfalls zum Abschluss", async () => {
      makeBackend();
      renderSetup();
      await toBranding();
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      expect(await screen.findByText(/Schritt 6 von 6/)).toBeInTheDocument();
    });

    it("Abschluss: Hinweis auf „Erste Schritte“, Zwei-Faktor-Link falls übersprungen, Notfall-Befehl, dann ins Cockpit", async () => {
      makeBackend();
      renderSetup();
      await toBranding();
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      await screen.findByText(/Schritt 6 von 6/);

      expect(screen.getByText(/Karte „Erste Schritte“/)).toBeInTheDocument();
      expect(await screen.findByRole("link", { name: /Zwei-Faktor jetzt einrichten/ })).toHaveAttribute("href", "/settings/account");
      expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password <benutzername>")).toBeInTheDocument();
      expect(document.body).toHaveTextContent(/Statt <benutzername> schreibst du deinen eigenen Benutzernamen, zum Beispiel admin/);
      expect(sessionStorage.getItem("deck.setup.step")).toBe("done");

      fireEvent.click(screen.getByRole("button", { name: "Weiter zum Dashboard" }));
      expect((await screen.findByTestId("where")).textContent).toBe("/");
      expect(sessionStorage.getItem("deck.setup.step")).toBeNull();
    });

    it("Abschluss ohne Zwei-Faktor-Hinweis, wenn sie eingerichtet ist", async () => {
      makeBackend({ totp: true });
      renderSetup();
      await toModules();
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      fireEvent.click(await screen.findByRole("button", { name: "Weiter" }));
      await screen.findByText(/Schritt 5 von 6/);
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      await screen.findByText(/Schritt 6 von 6/);
      await waitFor(() => expect(screen.getByTestId("setup-access")).toBeInTheDocument());
      expect(screen.queryByRole("link", { name: /Zwei-Faktor jetzt einrichten/ })).not.toBeInTheDocument();
    });
  });

  describe("Neuladen mitten im Assistenten", () => {
    it("merkt sich den erreichten Schritt pro Tab", async () => {
      makeBackend();
      renderSetup();
      await createAccount();
      expect(sessionStorage.getItem("deck.setup.step")).toBe("timezone");
      await screen.findByTestId("setup-timezone");
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      await screen.findByText(/Schritt 3 von 6/);
      expect(sessionStorage.getItem("deck.setup.step")).toBe("modules");
    });

    it("macht beim gemerkten Schritt weiter, wenn die Anmeldung noch gilt (Konto gibt es ja schon)", async () => {
      const backend = makeBackend({ bootstrapNeeded: false, refreshOk: true });
      sessionStorage.setItem("deck.setup.step", "modules");
      renderSetup();
      expect(await screen.findByText(/Schritt 3 von 6/)).toBeInTheDocument();
      await screen.findByTestId("module-system");
      expect(screen.queryByLabelText(/^Einrichtungscode/)).not.toBeInTheDocument();
      expect(backend.count("POST", "/auth/refresh")).toBe(1);
      expect(useAuthStore.getState().status).toBe("authenticated");
      // Und von dort geht es normal weiter.
      fireEvent.click(screen.getByRole("button", { name: "Überspringen" }));
      await screen.findByText(/Schritt 4 von 6/);
    });

    it("geht auf /login, wenn die Anmeldung abgelaufen ist, und vergisst den Schritt", async () => {
      makeBackend({ bootstrapNeeded: false, refreshOk: false });
      sessionStorage.setItem("deck.setup.step", "totp");
      renderSetup();
      expect((await screen.findByTestId("where")).textContent).toBe("/login");
      expect(sessionStorage.getItem("deck.setup.step")).toBeNull();
    });

    it("ohne gemerkten Schritt geht es wie bisher auf /login, ohne Anmeldung zu versuchen", async () => {
      const backend = makeBackend({ bootstrapNeeded: false, refreshOk: true });
      renderSetup();
      expect((await screen.findByTestId("where")).textContent).toBe("/login");
      expect(backend.count("POST", "/auth/refresh")).toBe(0);
    });

    it("ein unbrauchbarer gemerkter Wert (oder „account“) wird ignoriert", async () => {
      makeBackend({ bootstrapNeeded: false, refreshOk: true });
      sessionStorage.setItem("deck.setup.step", "account");
      renderSetup();
      expect((await screen.findByTestId("where")).textContent).toBe("/login");
    });
  });
});


describe("Sicherung einspielen im Assistenten", () => {
  const RID = "b".repeat(32);
  const SUMMARY = {
    created_at: "2026-10-01T03:00:00Z", app_version: "0.5.0", instance_id: "inst-abc", mode: "passwort", owner_name: "anna", users: 2, hosts: 5,
    extensions: [{ id: "proxmox", version: "1.0" }], includes: { runs: false, branding: true, jwt_secret: true }, warnings: [],
  };
  const STAGED = { id: RID, state: "uploaded", size: 5000, header: { mode: "passwort", created_at: "2026-10-01T03:00:00Z", app_version: "0.5.0" }, summary: null, expires_in: 3500 };

  type Seen = { method: string; path: string; headers: Record<string, string>; body: unknown };

  /** makeBackend plus the restore routes; `afterRestart` is what GET /auth/bootstrap answers after the restart. */
  function withRestore(opts: { afterRestart?: unknown; inspect?: () => Response } = {}) {
    const backend = makeBackend();
    const online = backend.fetchMock.getMockImplementation()!;
    const seen: Seen[] = [];
    let restarted = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
      const method = init?.method ?? "GET";
      const headers: Record<string, string> = {};
      new Headers(init?.headers).forEach((v, k) => { headers[k.toLowerCase()] = v; });
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      if (path.startsWith("/auth/bootstrap/") || path === "/health") seen.push({ method, path, headers, body });
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status });
      if (path === "/auth/bootstrap" && method === "GET" && restarted) return json(opts.afterRestart ?? { needed: false });
      if (path === `/auth/bootstrap/restore/${RID}/inspect`) return opts.inspect ? opts.inspect() : json({ ...STAGED, state: "ready", summary: SUMMARY });
      if (path === `/auth/bootstrap/restore/${RID}/schedule`) return json({ id: RID, source: "bootstrap", scheduled_at: "x", expires_in: 3500, sign_out_all: true, backup: {} });
      if (path === "/auth/bootstrap/restart") { restarted = true; return json({ restarting: true, exit_code: 75 }, 202); }
      if (path === "/auth/bootstrap/restore/pending") return new Response(null, { status: 204 });
      return online(input, init);
    }));
    return { backend, seen };
  }

  beforeEach(() => {
    restartTiming.pollMs = 1;
    restartTiming.timeoutMs = 300;
    vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
  });

  async function openRestore() {
    await screen.findByText("Willkommen");
    fireEvent.click(screen.getByRole("button", { name: "Oder: Sicherung einspielen" }));
    await screen.findByTestId("setup-progress");
  }

  it("bietet im ersten Schritt „Oder: Sicherung einspielen“ an und kommt wieder zurück", async () => {
    withRestore();
    renderSetup();
    await openRestore();
    expect(progress()).toBe("Sicherung einspielen");
    expect(screen.queryByLabelText("Passwort bestätigen")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Einrichtungscode")).toBeInTheDocument();
    expect(screen.getByText("Wo finde ich den Code?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zurück" }));
    expect(await screen.findByLabelText("Passwort bestätigen")).toBeInTheDocument();
    expect(progress()).toBe("Schritt 1 von 6 — Administrator-Konto anlegen");
  });

  it("derselbe Ablauf über die Assistenten-Endpunkte: Code in jedem Aufruf, nie ein Konto, am Ende zur Anmeldung", async () => {
    const { backend, seen } = withRestore();
    const send = vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 201, text: JSON.stringify(STAGED) });
    const health = vi.spyOn(restartProbe, "health");
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 300 });
    health.mockResolvedValueOnce(null);
    health.mockResolvedValue({ status: "ok", uptime_s: 2 });
    renderSetup();
    await openRestore();

    const upload = screen.getByRole("button", { name: "Hochladen" });
    expect(upload).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Sicherungsdatei"), { target: { files: [new File([new Uint8Array(5000)], "x.ndbak")] } });
    expect(upload).toBeDisabled(); // Code fehlt
    fireEvent.change(screen.getByLabelText("Einrichtungscode"), { target: { value: "abcd-efgh-jkmn" } });
    expect(screen.getByLabelText("Einrichtungscode")).toHaveValue("ABCD-EFGH-JKMN");
    expect(upload).toBeEnabled();
    fireEvent.click(upload);

    await screen.findByText(/Die Datei ist angekommen/);
    const req = send.mock.calls[0][0];
    expect(req.url).toBe("/api/v1/auth/bootstrap/restore/upload");
    expect(req.headers).toEqual({ "X-Setup-Code": "ABCD-EFGH-JKMN" });
    fireEvent.change(screen.getByLabelText("Passwort der Sicherung"), { target: { value: "einmal-passwort-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));

    await screen.findByText("Das steckt in der Sicherung");
    expect(screen.getByText("anna")).toBeInTheDocument();
    // Im Assistenten wird geduzt, und es gibt kein Konto-Passwort zum Bestätigen.
    expect(screen.getByText(/Spiele nur Sicherungen ein, die du selbst erstellt hast/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Anmeldepasswort zur Bestätigung")).not.toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Einspielen und neu starten" });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /Ich habe verstanden/ }));
    fireEvent.click(apply);

    await waitFor(() => expect(browserNavigation.assign).toHaveBeenCalledWith("/login"));
    const calls = seen.filter((c) => c.path.startsWith("/auth/bootstrap/") && c.path !== "/auth/bootstrap");
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      `POST /auth/bootstrap/restore/${RID}/inspect`, `POST /auth/bootstrap/restore/${RID}/schedule`, "POST /auth/bootstrap/restart",
    ]);
    for (const call of calls) {
      expect(call.headers["x-setup-code"]).toBe("ABCD-EFGH-JKMN");
      expect(call.headers["authorization"]).toBeUndefined();
    }
    expect(calls[0].body).toEqual({ password: "einmal-passwort-123" });
    expect(calls[1].body).toEqual({ sign_out_all: true });
    expect(document.body.innerHTML).not.toContain("einmal-passwort-123");
    // Ohne Konto fragt der Ablauf auch nicht, ob Zwei-Faktor an ist.
    expect(backend.count("GET", "/me")).toBe(0);
  });

  it("falscher Einrichtungscode: die Meldung des Servers, es geht nicht weiter", async () => {
    withRestore();
    vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 403, text: JSON.stringify({ detail: "Der Einrichtungscode stimmt nicht." }) });
    renderSetup();
    await openRestore();
    fireEvent.change(screen.getByLabelText("Sicherungsdatei"), { target: { files: [new File([new Uint8Array(5)], "x.ndbak")] } });
    fireEvent.change(screen.getByLabelText("Einrichtungscode"), { target: { value: "FALSCH-FALSCH-FALS" } });
    fireEvent.click(screen.getByRole("button", { name: "Hochladen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Der Einrichtungscode stimmt nicht.");
    expect(screen.getByLabelText("Einrichtungscode")).toHaveValue("FALSCH-FALSCH-FALS"); // bleibt zum Korrigieren
  });

  it("hat der Neustart nichts eingespielt (Rückweg), erklärt der Assistent warum und bleibt auf der Seite", async () => {
    withRestore({ afterRestart: { needed: true, restore: { ok: false, message: "Die Sicherung ließ sich nicht auf den Stand dieser Version bringen.", at: "x" } } });
    vi.spyOn(uploadTransport, "send").mockResolvedValue({ status: 201, text: JSON.stringify(STAGED) });
    const health = vi.spyOn(restartProbe, "health");
    health.mockResolvedValueOnce({ status: "ok", uptime_s: 300 });
    health.mockResolvedValueOnce(null);
    health.mockResolvedValue({ status: "ok", uptime_s: 2 });
    renderSetup();
    await openRestore();
    fireEvent.change(screen.getByLabelText("Sicherungsdatei"), { target: { files: [new File([new Uint8Array(5)], "x.ndbak")] } });
    fireEvent.change(screen.getByLabelText("Einrichtungscode"), { target: { value: "ABCD-EFGH-JKMN" } });
    fireEvent.click(screen.getByRole("button", { name: "Hochladen" }));
    await screen.findByText(/Die Datei ist angekommen/);
    fireEvent.change(screen.getByLabelText("Passwort der Sicherung"), { target: { value: "einmal-passwort-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await screen.findByText("Das steckt in der Sicherung");
    fireEvent.click(screen.getByRole("checkbox", { name: /Ich habe verstanden/ }));
    fireEvent.click(screen.getByRole("button", { name: "Einspielen und neu starten" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Das Einspielen hat nicht geklappt: Die Sicherung ließ sich nicht auf den Stand dieser Version bringen\. Es wurde nichts verändert\./);
    expect(browserNavigation.assign).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Hochladen" })).toBeInTheDocument();
  });
});
