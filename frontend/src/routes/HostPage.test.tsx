import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { promptDialog } from "../state/dialogs";
import { HostPage } from "./HostPage";

vi.mock("../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

type Call = { path: string; method: string; body: unknown };

/** Dieselben Daten wie die Design-Vorschau; `overrides` gewinnt je Pfad (eine Funktion
 * wird bei jedem Aufruf neu gefragt), `statuses` setzt fuer einen Pfad einen anderen
 * HTTP-Status als 200. */
function mockFetch(overrides: Record<string, unknown> = {}, calls: Call[] = [], statuses: Record<string, number> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    calls.push({ path: key, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const raw = key in overrides ? overrides[key] : respond(path, method);
    const body = typeof raw === "function" ? (raw as () => unknown)() : raw;
    if (body === undefined) return new Response(JSON.stringify({ detail: "nicht da" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: statuses[key] ?? 200 });
  });
}

function renderHost(hostId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hosts/${hostId}`]}>
        <Routes>
          <Route path="/hosts/:hostId" element={<HostPage />} />
          <Route path="/" element={<p data-testid="uebersicht">Übersicht</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Wie im echten Router: dieselbe Route fuer alle Server -- ein Wechsel per Link
 * (Strg+K-Palette, Browser-Zurueck) baut `HostPage` nicht neu auf. */
function renderHostWithNav(hostId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hosts/${hostId}`]}>
        <Link to="/hosts/g-monitoring">Wechsel zu monitoring</Link>
        <Link to="/hosts/g-docker">Wechsel zu docker</Link>
        <Routes>
          <Route path="/hosts/:hostId" element={<HostPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: permissions.includes("*"), locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

beforeEach(() => login(["*"]));

afterEach(() => {
  vi.useRealTimers();
});

describe("HostPage (Server-Seite, Plesk-Stil)", () => {
  it("Markierungen bearbeiten: PATCH mit der Liste, danach Host neu laden", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({}, calls));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Markierungen bearbeiten" }));
    const input = screen.getByLabelText("Markierungen");
    expect(input).toHaveValue("gameserver, windows");
    expect(screen.getByText(/docker \(Service-Matrix\), gameserver \(Gameserver\)/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "Gameserver, docker" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.path).toBe("/hosts/g-valheim");
    expect(patch.body).toEqual({ tags: ["gameserver", "docker"] });
    await waitFor(() => expect(calls.filter((c) => c.path === "/hosts/g-valheim" && c.method === "GET").length).toBeGreaterThan(1));
  });

  it("Markierungen: ohne hosts.write kein Editor", async () => {
    login(["hosts.read"]);
    vi.stubGlobal("fetch", mockFetch());
    renderHost("g-valheim");
    await screen.findByRole("heading", { level: 1, name: /game-win/ });
    expect(screen.queryByRole("button", { name: "Markierungen bearbeiten" })).not.toBeInTheDocument();
  });

  it("VM: Kopf, Werkzeuge nach Bereich gruppiert, Kern-Kacheln Konsole/Terminal", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderHost("g-valheim");
    expect(await screen.findByRole("heading", { level: 1, name: /game-win/ })).toBeInTheDocument();
    expect(screen.getByText(/Virtuelle Maschine · 192\.168\.2\.24 · läuft/)).toBeInTheDocument();
    expect(screen.getByText("gameserver")).toBeInTheDocument();

    const tools = screen.getByTestId("host-tools");
    await waitFor(() => expect(within(tools).getByRole("link", { name: /Hardware, Netzwerk & Snapshots/ })).toBeInTheDocument());
    const groups = [...tools.children].map((g) => g.querySelector("p")?.textContent);
    expect(groups).toEqual(["Steuerung", "Überwachung", "Dienste", "Daten & Sicherungen", "Einstellungen"]);
    expect(within(tools).getByRole("link", { name: /Konsole/ })).toHaveAttribute("href", "/console/g-valheim");
    await waitFor(() => expect(within(tools).getByRole("link", { name: /Terminal/ })).toHaveAttribute("href", "/terminal?host=g-valheim"));
    expect(within(tools).getByRole("link", { name: /Gameserver/ })).toHaveAttribute("href", "/ext/gameserver/gameservers?host=g-valheim");
    expect(within(tools).getByRole("link", { name: /Aufgabenverlauf/ })).toHaveAttribute("href", "/ext/proxmox/nodes?host=g-valheim&tasks=1");
  });

  it("Aktionen: eigene des Hosts als Knöpfe, allgemeine mit Eingabe eingeklappt", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderHost("g-valheim");
    const quick = await screen.findByTestId("host-actions");
    expect(within(quick).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Starten", "Neustarten", "Snapshot erstellen", "Snapshot zurückrollen …",
    ]);
    const more = screen.getByTestId("host-more-actions");
    expect(more.querySelector("summary")?.textContent).toBe("Weitere Aktionen (2)");
    expect(within(more).getByRole("button", { name: "Shell-Befehl ausführen …" })).toBeInTheDocument();
  });

  it("Aktion mit Pflichtfeld: Formular, Auslösen über das Gate, Selbstfreigabe mit Recht", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.snapshot_rollback": { id: "a1", status: "proposed", risk: "high" },
      "/actions/a1/approve": { id: "a1", status: "succeeded", risk: "high" },
    }, calls));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Snapshot zurückrollen …" }));
    fireEvent.change(screen.getByLabelText("snapname"), { target: { value: "vor-update" } });
    fireEvent.click(screen.getByRole("button", { name: "Ausführen" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Snapshot zurückrollen: Ausgeführt"));
    const trigger = calls.find((c) => c.path === "/hosts/g-valheim/actions/vm.snapshot_rollback");
    expect(trigger?.method).toBe("POST");
    expect(trigger?.body).toMatchObject({ payload: { snapname: "vor-update" } });
    expect(calls.some((c) => c.path === "/actions/a1/approve" && c.method === "POST")).toBe(true);
  });

  it("Gate-Sperre (403 mit ActionOut): Begründung statt „HTTP 403“", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.reboot": {
        id: "a9", status: "denied", risk: "high", result: {},
        gate_decision: { rule: "flap_limit", detail: "Bereits mehrfach in kurzer Zeit versucht (Fingerprint 1a2b3c4d5e6f...)." },
      },
    }, calls, { "/hosts/g-valheim/actions/vm.reboot": 403 }));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Neustarten" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(
      "Neustarten: Gesperrt: Bereits mehrfach in kurzer Zeit versucht (Fingerprint 1a2b3c4d5e6f...).",
    ));
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
  });

  it("Fehlgeschlagene Ausführung: deutscher Text mit Grund statt „failed“", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.start": { id: "a2", status: "proposed", risk: "low" },
      "/actions/a2/approve": { id: "a2", status: "failed", risk: "low", gate_decision: { rule: "autonomy:propose" }, result: { success: false, error: "VM ist gesperrt (Backup läuft)." } },
    }));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Starten" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Starten: Fehlgeschlagen: VM ist gesperrt (Backup läuft)."));
  });

  it("Sofort ausgeführt (Vollautonomie) und gescheitert: Grund aus der direkten Antwort", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.snapshot": { id: "a3", status: "failed", risk: "medium", gate_decision: { rule: "autonomy:full" }, result: { success: false, error: "Speicher voll." } },
    }));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Snapshot erstellen" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Snapshot erstellen: Fehlgeschlagen: Speicher voll."));
  });

  it("Lange Aktion (202 „läuft“): Zwischenstand, dann das Ergebnis per Nachfrage", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let polls = 0;
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.start": { id: "a2", status: "proposed", risk: "low" },
      "/actions/a2/approve": { id: "a2", status: "executing", risk: "low", gate_decision: {}, result: {} },
      "/actions/a2": () => {
        polls += 1;
        return polls < 2
          ? { id: "a2", status: "executing", risk: "low", gate_decision: {}, result: {} }
          : { id: "a2", status: "succeeded", risk: "low", gate_decision: {}, result: { success: true } };
      },
    }, calls, { "/actions/a2/approve": 202 }));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Starten" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/^Starten: Läuft im Hintergrund/));
    expect(screen.getByRole("button", { name: "…" })).toBeDisabled();

    await act(() => vi.advanceTimersByTimeAsync(6000));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Starten: Ausgeführt"));
    expect(polls).toBe(2);
    expect(screen.getByRole("button", { name: "Starten" })).not.toBeDisabled();
  });

  it("Sofort ausgeführt (Vollautonomie), aber noch nicht fertig: fragt ebenfalls nach", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.snapshot": { id: "a3", status: "executing", risk: "medium", gate_decision: { rule: "autonomy:full" }, result: {} },
      "/actions/a3": { id: "a3", status: "failed", risk: "medium", gate_decision: { rule: "autonomy:full" }, result: { success: false, error: "Speicher voll." } },
    }, [], { "/hosts/g-valheim/actions/vm.snapshot": 202 }));
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Snapshot erstellen" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/Läuft im Hintergrund/));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Snapshot erstellen: Fehlgeschlagen: Speicher voll."));
  });

  it("Serverwechsel: Meldung und laufende Aktion des alten Servers erscheinen nicht beim neuen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-docker/actions/vm.reboot": { id: "a7", status: "proposed", risk: "high" },
      "/actions/a7/approve": { id: "a7", status: "executing", risk: "high", gate_decision: {}, result: {} },
      "/actions/a7": { id: "a7", status: "succeeded", risk: "high", gate_decision: {}, result: { success: true } },
    }, calls, { "/actions/a7/approve": 202 }));
    renderHostWithNav("g-docker");
    fireEvent.click(await screen.findByRole("button", { name: "Neustarten" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/^Neustarten: Läuft im Hintergrund/));

    fireEvent.click(screen.getByRole("link", { name: "Wechsel zu monitoring" }));
    await screen.findByRole("heading", { level: 1, name: /monitoring/ });

    // Auf dem neuen Server: keine fremde Meldung, Knopf frei.
    expect(screen.queryByRole("status")).toBeNull();
    expect(await screen.findByRole("button", { name: "Neustarten" })).not.toBeDisabled();

    // Das Ergebnis des Docker-Neustarts darf nicht mehr auftauchen, gefragt wird nicht mehr.
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(screen.queryByRole("status")).toBeNull();
    expect(calls.filter((c) => c.path === "/actions/a7" && c.method === "GET")).toHaveLength(0);
  });

  it("Serverwechsel: auch eine fertige Meldung bleibt nicht beim neuen Server stehen", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-docker/actions/vm.reboot": { id: "a8", status: "proposed", risk: "high" },
      "/actions/a8/approve": { id: "a8", status: "succeeded", risk: "high" },
    }));
    renderHostWithNav("g-docker");
    fireEvent.click(await screen.findByRole("button", { name: "Neustarten" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Neustarten: Ausgeführt"));

    fireEvent.click(screen.getByRole("link", { name: "Wechsel zu monitoring" }));
    await screen.findByRole("heading", { level: 1, name: /monitoring/ });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("Zwei Aktionen: Zwischenstand und Ergebnis der älteren ersetzen den Fehlschlag der neueren nicht", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let releaseStart = (_res: Response) => {};
    const base = mockFetch({
      "/hosts/g-valheim/actions/vm.start": { id: "a5", status: "proposed", risk: "low" },
      "/actions/a5": { id: "a5", status: "succeeded", risk: "low", gate_decision: {}, result: { success: true } },
      "/hosts/g-valheim/actions/vm.snapshot": { id: "a6", status: "failed", risk: "medium", gate_decision: { rule: "autonomy:full" }, result: { success: false, error: "Speicher voll." } },
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      // Die Freigabe von "Starten" haengt, bis der Test sie beantwortet.
      if (url.endsWith("/actions/a5/approve")) return new Promise<Response>((resolve) => { releaseStart = resolve; });
      return base(input, init);
    }));
    const texts = () => screen.queryAllByRole("status").map((el) => el.textContent);
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Starten" }));
    fireEvent.click(await screen.findByRole("button", { name: "Snapshot erstellen" }));
    await waitFor(() => expect(texts()).toEqual(["Snapshot erstellen: Fehlgeschlagen: Speicher voll."]));

    // Die spaete Antwort der aelteren Aktion: eigene Zeile, der Fehlschlag bleibt stehen.
    await act(async () => {
      releaseStart(new Response(JSON.stringify({ id: "a5", status: "executing", risk: "low", gate_decision: {}, result: {} }), { status: 202 }));
    });
    await waitFor(() => expect(texts()).toHaveLength(2));
    expect(texts()[0]).toMatch(/^Starten: Läuft im Hintergrund/);
    expect(texts()[1]).toBe("Snapshot erstellen: Fehlgeschlagen: Speicher voll.");

    // Ihr Ergebnis ersetzt nur die eigene Zeile.
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(texts()).toEqual(["Starten: Ausgeführt", "Snapshot erstellen: Fehlgeschlagen: Speicher voll."]));
  });

  it("Neue Aktion: fertige Meldungen früherer Aktionen verschwinden", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/g-valheim/actions/vm.snapshot": { id: "a6", status: "failed", risk: "medium", gate_decision: { rule: "autonomy:full" }, result: { success: false, error: "Speicher voll." } },
      "/hosts/g-valheim/actions/vm.start": { id: "a2", status: "proposed", risk: "low" },
      "/actions/a2/approve": { id: "a2", status: "succeeded", risk: "low", gate_decision: {}, result: { success: true } },
    }));
    const texts = () => screen.queryAllByRole("status").map((el) => el.textContent);
    renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Snapshot erstellen" }));
    await waitFor(() => expect(texts()).toEqual(["Snapshot erstellen: Fehlgeschlagen: Speicher voll."]));

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    await waitFor(() => expect(texts()).toEqual(["Starten: Ausgeführt"]));
  });

  it("Seite verlassen, bevor die Freigabe antwortet: es wird nicht weiter nachgefragt", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let release = (_res: Response) => {};
    const calls: Call[] = [];
    const base = mockFetch({
      "/hosts/g-valheim/actions/vm.start": { id: "a5", status: "proposed", risk: "low" },
      "/actions/a5": { id: "a5", status: "executing", risk: "low", gate_decision: {}, result: {} },
    }, calls);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      // Die Freigabe wartet bis zu 20 s -- so lange steht der Test still.
      if (url.endsWith("/actions/a5/approve")) {
        calls.push({ path: "/actions/a5/approve", method: "POST", body: undefined });
        return new Promise<Response>((resolve) => { release = resolve; });
      }
      return base(input, init);
    }));
    const { unmount } = renderHost("g-valheim");
    fireEvent.click(await screen.findByRole("button", { name: "Starten" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/actions/a5/approve")).toBe(true));
    unmount();
    await act(async () => {
      release(new Response(JSON.stringify({ id: "a5", status: "executing", risk: "low", gate_decision: {}, result: {} }), { status: 202 }));
    });

    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(calls.filter((c) => c.path === "/actions/a5" && c.method === "GET")).toHaveLength(0);
  });

  it("Pi (kein Gast): keine Konsole, keine VM-Aktionen, seine Dienste und Meldungen", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/notifications?limit=100": [
        { id: "n1", ts: new Date().toISOString(), severity: "warning", title: "Pi: Speicher knapp", payload: { host_id: "h-pi" } },
        { id: "n2", ts: new Date().toISOString(), severity: "warning", title: "Fremder Host", payload: { host_id: "g-docker" } },
      ],
    }));
    renderHost("h-pi");
    await screen.findByRole("heading", { level: 1, name: /Raspberry Pi/ });
    await waitFor(() => expect(screen.getByTestId("host-tools").textContent).toContain("Skripte"));
    expect(within(screen.getByTestId("host-tools")).queryByRole("link", { name: /Konsole/ })).toBeNull();
    await waitFor(() => expect(screen.getByTestId("host-more-actions")).toBeInTheDocument());
    expect(screen.queryByTestId("host-actions")).toBeNull();

    await waitFor(() => expect(screen.getByTestId("host-services").children.length).toBe(4));
    const notes = await screen.findByTestId("host-notifications");
    expect(notes.textContent).toContain("Pi: Speicher knapp");
    expect(notes.textContent).not.toContain("Fremder Host");
  });

  it("Terminal nur für Hosts aus /terminal/hosts (Liste von IDs)", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/terminal/hosts": ["h-x", "g-valheim"] }));
    renderHost("g-valheim");
    const tools = await screen.findByTestId("host-tools");
    await waitFor(() => expect(within(tools).getByRole("link", { name: /Terminal/ })).toHaveAttribute("href", "/terminal?host=g-valheim"));
    expect(screen.getAllByRole("link", { name: /Terminal/ }).map((a) => a.getAttribute("href"))).toEqual([
      "/terminal?host=g-valheim", "/terminal?host=g-valheim",
    ]);
  });

  it("Host ohne Terminal-Zugang: kein Terminal-Knopf", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/terminal/hosts": ["h-x"] }));
    renderHost("g-valheim");
    await waitFor(() => expect(screen.getByTestId("host-tools").textContent).toContain("Gameserver"));
    expect(screen.queryByRole("link", { name: /Terminal/ })).toBeNull();
  });

  it("Betrachter ohne hosts.execute: keine Aktionen, kein Terminal", async () => {
    login(["hosts.read"]);
    vi.stubGlobal("fetch", mockFetch());
    renderHost("g-valheim");
    await screen.findByRole("heading", { level: 1, name: /game-win/ });
    await waitFor(() => expect(screen.getByTestId("host-tools").textContent).toContain("Gameserver"));
    expect(screen.queryByTestId("host-actions")).toBeNull();
    expect(screen.queryByTestId("host-more-actions")).toBeNull();
    expect(within(screen.getByTestId("host-tools")).queryByRole("link", { name: /Terminal/ })).toBeNull();
  });

  it("Server verwalten: „Bearbeiten“ führt zur Einstellungsseite, Zugang als Karte mit Prüfen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({}, calls));
    renderHost("g-docker");
    await screen.findByRole("heading", { level: 1, name: /docker/ });
    expect(screen.getByRole("link", { name: "Bearbeiten" })).toHaveAttribute("href", "/settings/hosts/g-docker");
    const card = (await screen.findByRole("heading", { level: 2, name: "Zugang" })).closest("section") as HTMLElement;
    expect(within(card).getByText("Schlüssel · lattice")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts/g-docker");
    fireEvent.click(within(card).getByRole("button", { name: "Verbindung prüfen" }));
    expect(await within(card).findByText("Anmeldung als lattice")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/hosts/g-docker/check")).toBe(true);
  });

  it("Server ohne Zugang: „Kein Zugang“ in der Karte", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderHost("g-ki");
    const card = (await screen.findByRole("heading", { level: 2, name: "Zugang" })).closest("section") as HTMLElement;
    expect(within(card).getByText("Kein Zugang")).toBeInTheDocument();
  });

  it("ohne hosts.write: weder Bearbeiten noch Löschen noch Zugangs-Karte", async () => {
    login(["hosts.read", "hosts.execute"]);
    vi.stubGlobal("fetch", mockFetch());
    renderHost("g-docker");
    await screen.findByRole("heading", { level: 1, name: /docker/ });
    expect(screen.queryByRole("link", { name: "Bearbeiten" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Löschen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Zugang" })).not.toBeInTheDocument();
  });

  it("Löschen: Kurzname eintippen, danach zurück zur Übersicht; ein falscher Name löscht nichts", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", mockFetch({}, calls));
    renderHost("g-docker");
    const button = await screen.findByRole("button", { name: "Löschen" });
    vi.mocked(promptDialog).mockResolvedValueOnce("falsch");
    fireEvent.click(button);
    expect(await screen.findByText(/Der Kurzname stimmte nicht mit „docker“ überein/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    vi.mocked(promptDialog).mockResolvedValueOnce("docker");
    fireEvent.click(button);
    expect(await screen.findByTestId("uebersicht")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/hosts/g-docker"]);
  });

  it("Unbekannter Host", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderHost("gibt-es-nicht");
    expect(await screen.findByText("Server nicht gefunden.")).toBeInTheDocument();
  });
});

describe("HostPage ohne Werkzeuge", () => {
  it("erklärt, woher Werkzeuge kommen, mit Knopf zu den Modulen", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/hosts/h-pi/tools": [], "/terminal/hosts": [] }));
    renderHost("h-pi");
    const empty = await screen.findByTestId("host-no-tools");
    expect(empty.textContent).toContain("Noch keine Werkzeuge für diesen Server");
    expect(within(empty).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
  });

  it("ohne Recht extensions.manage nur der Text", async () => {
    login(["hosts.read", "hosts.write", "hosts.execute"]);
    vi.stubGlobal("fetch", mockFetch({ "/hosts/h-pi/tools": [], "/terminal/hosts": [] }));
    renderHost("h-pi");
    const empty = await screen.findByTestId("host-no-tools");
    expect(empty.textContent).toContain("Administrator");
    expect(within(empty).queryByRole("link")).toBeNull();
  });
});
