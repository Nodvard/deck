import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { previewState, respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { Cockpit } from "./Cockpit";

const confirmDialog = vi.hoisted(() => vi.fn(async (..._args: unknown[]) => true));
vi.mock("../state/dialogs", () => ({ confirmDialog, promptDialog: vi.fn(async () => null) }));

/** Dieselben, einem typischen Homelab nachgebildeten Daten wie die Design-Vorschau. */
function mockFetch(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    const sent = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const body = key in overrides ? overrides[key] : respond(path, init?.method ?? "GET", sent);
    if (body === undefined) throw new Error(`Unerwarteter Fetch: ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

function renderCockpit() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Cockpit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function login(permissions: string[], isOwner = true) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico Benks", email: null, is_owner: isOwner, locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

beforeEach(() => {
  login(["*"]);
  previewState.noHosts = false;
  previewState.start = false;
  previewState.firstStepsDismissed = false;
  previewState.demo = false;
  previewState.noApps = false;
  previewState.customApps = null;
  confirmDialog.mockClear();
  confirmDialog.mockImplementation(async () => true);
});

describe("Cockpit", () => {
  it("Lagebild: Begrüßung, was Aufmerksamkeit braucht, Kennzahlen", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderCockpit();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/, Nico$/);
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("2 Dinge brauchen deine Aufmerksamkeit"));
    expect(screen.getByTestId("stat-Server").textContent).toContain("9/9");
    expect(screen.getByTestId("stat-Dienste").textContent).toContain("13/14");
    expect(screen.getByTestId("stat-Backups").textContent).toContain("3/5");
    expect(screen.getByTestId("stat-Backups").textContent).toContain("2 noch ohne Lauf");
    expect(screen.getByTestId("stat-Freigaben").textContent).toContain("warten auf dich");
    const attention = screen.getByTestId("attention");
    expect(attention.textContent).toContain("Eine Aktion wartet auf Freigabe");
    expect(attention.textContent).toContain("Datenträger auf pve1: 28 % Rest");
  });

  it("Infrastruktur: Knoten mit Live-Auslastung, Maschinen mit Konsole/Terminal", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderCockpit();
    const pve2 = await screen.findByTestId("node-n-pve2");
    await waitFor(() => expect(within(pve2).getByRole("img", { name: "CPU 14 %" })).toBeInTheDocument());
    expect(within(pve2).getByRole("img", { name: "RAM 83 %" })).toBeInTheDocument();
    expect(pve2.textContent).toContain("läuft seit 10 h 38 min");

    // Name fuehrt auf die Server-Seite (Plesk-Stil) -- Knoten wie Maschinen.
    expect(within(pve2).getByRole("link", { name: "pve2" })).toHaveAttribute("href", "/hosts/n-pve2");
    const valheim = await screen.findByTestId("machine-g-valheim");
    expect(within(valheim).getByRole("link", { name: "game-win" })).toHaveAttribute("href", "/hosts/g-valheim");
    expect(within(valheim).getByRole("link", { name: "Konsole game-win" })).toHaveAttribute("href", "/console/g-valheim");
    await waitFor(() => expect(within(valheim).getByRole("link", { name: "Terminal game-win" })).toHaveAttribute("href", "/terminal?host=g-valheim"));
    // Der Pi hat Messwerte aus dem Verlauf -> Karte wie die Knoten (statt Zeile); weder VM noch Container -> keine Konsole.
    const pi = await screen.findByTestId("server-h-pi");
    expect(screen.queryByTestId("machine-h-pi")).toBeNull();
    expect(within(pi).queryByRole("link", { name: /Konsole/ })).toBeNull();
    await waitFor(() => expect(within(pi).getByRole("link", { name: "Terminal Raspberry Pi" })).toHaveAttribute("href", "/terminal?host=h-pi"));
  });

  it("Apps: nur mit Web-Oberfläche, auf Wunsch alle, filterbar", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderCockpit();
    const apps = await screen.findByTestId("apps");
    expect(within(apps).getByTestId("app-docker:grafana")).toHaveAttribute("href", "http://192.168.2.21:3000");
    expect(within(apps).queryByTestId("app-docker:redis")).toBeNull();

    fireEvent.click(screen.getByLabelText("auch ohne Web-Oberfläche"));
    expect(within(screen.getByTestId("apps")).getByTestId("app-docker:redis")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Apps filtern"), { target: { value: "pi" } });
    const names = within(screen.getByTestId("apps")).getAllByTestId(/^app-/).map((el) => el.getAttribute("data-testid"));
    expect(names).toEqual(expect.arrayContaining(["app-Raspberry Pi:pihole", "app-Raspberry Pi:npm-nginx-1"]));
    expect(names).not.toContain("app-docker:grafana");
  });

  it("alles gut: 'Alles läuft rund' und leere Aufmerksamkeits-Liste", async () => {
    const calm = { ...(respond("/api/v1/overview", "GET") as object), pending_actions: 0, attention: [] };
    vi.stubGlobal("fetch", mockFetch({ "/overview": calm }));
    renderCockpit();
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Alles läuft rund. 9 von 9 Servern online, 13 Dienste aktiv."));
    expect(screen.getByTestId("attention").textContent).toContain("Nichts offen");
  });

  it("alle Docker-Server nicht erreichbar: „unbekannt“ statt 0 von 3", async () => {
    const base = respond("/api/v1/overview", "GET") as { services: object[] };
    const dead = ["a", "b", "c"].map((h) => ({
      id: `${h}:__error__`, name: `⚠ ${h}`, host: h, host_id: null, state: "error", tone: "danger", url: null, image: null, unreachable: true,
    }));
    const overview = { ...base, pending_actions: 0, attention: [], services: dead, services_running: 0, services_unreachable_hosts: ["a", "b", "c"] };
    vi.stubGlobal("fetch", mockFetch({ "/overview": overview }));
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("stat-Dienste").textContent).toContain("Container von 3 Servern nicht abrufbar"));
    const stat = screen.getByTestId("stat-Dienste").textContent ?? "";
    expect(stat).toContain("unbekannt");
    expect(stat).not.toContain("0/");
    expect(screen.getByTestId("cockpit-status").textContent).not.toContain("Dienste aktiv");
  });

  it("nur ein Docker-Server nicht erreichbar: die übrigen zählen, der Ausfall steht daneben", async () => {
    const base = respond("/api/v1/overview", "GET") as { services: object[] };
    const live = (n: string, state: string) => ({ id: `h1:${n}`, name: n, host: "h1", host_id: "h1", state, tone: null, url: null, image: null });
    const dead = { id: "h2:__error__", name: "⚠ h2", host: "h2", host_id: null, state: "error", tone: "danger", url: null, image: null, unreachable: true };
    const overview = {
      ...base, pending_actions: 0, attention: [], services: [live("a", "running"), live("b", "running"), live("c", "exited"), dead],
      services_running: 2, services_unreachable_hosts: ["h2"],
    };
    vi.stubGlobal("fetch", mockFetch({ "/overview": overview }));
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("stat-Dienste").textContent).toContain("2/3"));
    expect(screen.getByTestId("stat-Dienste").textContent).toContain("Container von 1 Server nicht abrufbar");
    // Kachel gelb und Überschrift „Alles läuft rund“ passen nicht zusammen: der Server steht unter „Braucht Aufmerksamkeit“.
    expect(screen.getByTestId("cockpit-status").textContent).not.toContain("Alles läuft rund");
    expect(screen.getByTestId("attention").textContent).toContain("Container nicht abrufbar: h2");
  });

  it("Backup-Verbindung nicht erreichbar: Warnung statt eines Jobs, zählt als Aufmerksamkeit", async () => {
    const base = respond("/api/v1/overview", "GET") as { backups: object };
    const overview = {
      ...base,
      pending_actions: 0,
      attention: [],
      backups: { total: 3, ok: 3, failed: 0, running: 0, unknown: 0, failed_names: [], unreachable_names: ["pve1"] },
    };
    vi.stubGlobal("fetch", mockFetch({ "/overview": overview }));
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Eine Sache braucht deine Aufmerksamkeit"));
    const stat = screen.getByTestId("stat-Backups").textContent ?? "";
    expect(stat).toContain("3/3");
    expect(stat).toContain("Verbindung nicht erreichbar");
    expect(stat).not.toContain("alle erfolgreich");
    expect(screen.getByTestId("attention").textContent).toContain("Backup-Verbindung nicht erreichbar: pve1");
  });

  it("nur tote Backup-Verbindungen: keine Zahl 0/0, sondern die Warnung", async () => {
    const base = respond("/api/v1/overview", "GET") as { backups: object };
    const overview = {
      ...base,
      pending_actions: 0,
      attention: [],
      backups: { total: 0, ok: 0, failed: 0, running: 0, unknown: 0, failed_names: [], unreachable_names: ["pve1", "pve2"] },
    };
    vi.stubGlobal("fetch", mockFetch({ "/overview": overview }));
    renderCockpit();

    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("2 Dinge brauchen deine Aufmerksamkeit"));
    const stat = screen.getByTestId("stat-Backups").textContent ?? "";
    expect(stat).toContain("–");
    expect(stat).not.toContain("0/0");
    expect(stat).toContain("2 Verbindungen nicht erreichbar");
  });

  it("ohne hosts.read: nur Module, keine Infrastruktur-Abfragen", async () => {
    login(["notifications.read"], false);
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCockpit();
    expect(await screen.findByTestId("modules")).toBeInTheDocument();
    expect(screen.queryByTestId("stat-Server")).toBeNull();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.includes("/hosts") || u.includes("/overview"))).toBe(false);
  });

  it("ohne einen einzigen Server: kein „Alles läuft rund“, sondern ein Leerzustand mit Knopf", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/hosts": [], "/overview": { ...(respond("/api/v1/overview", "GET") as object), services: [], services_running: 0, backups: null, pending_actions: 0, attention: [] } }));
    renderCockpit();
    const empty = await screen.findByTestId("cockpit-no-hosts");
    expect(screen.getByTestId("cockpit-status").textContent).toBe("Noch kein Server eingerichtet.");
    expect(screen.getByTestId("cockpit-status").textContent).not.toContain("Alles läuft rund");
    expect(screen.getByTestId("stat-Server").textContent).not.toContain("0/0");
    expect(within(empty).getByRole("link", { name: "Server hinzufügen" })).toHaveAttribute("href", "/settings/hosts?neu=1");
    expect(document.body.textContent).not.toContain("Noch keine Hosts bekannt");
  });

  it("Leerzustand: „Mit Beispieldaten ansehen“ legt Beispieldaten an, danach sieht das Cockpit gefüllt aus und das Band erscheint", async () => {
    previewState.noHosts = true;
    vi.stubGlobal("fetch", mockFetch());
    renderCockpit();
    const empty = await screen.findByTestId("cockpit-no-hosts");
    fireEvent.click(within(empty).getByRole("button", { name: "Mit Beispieldaten ansehen" }));
    expect(await screen.findByTestId("machine-demo-nas")).toBeInTheDocument();
    expect(screen.queryByTestId("cockpit-no-hosts")).toBeNull();
    expect(screen.getByTestId("machine-demo-sicherung").textContent).toContain("192.0.2.13");
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Dinge brauchen deine Aufmerksamkeit"));
    expect(screen.getByTestId("stat-Server").textContent).toContain("3/5");
  });

  it("Leerzustand ohne Recht auf Beispieldaten (nur hosts.write): Knopf „Server hinzufügen“, aber kein Beispiel-Knopf", async () => {
    login(["hosts.read", "hosts.write"], false);
    vi.stubGlobal("fetch", mockFetch({ "/hosts": [], "/overview": { ...(respond("/api/v1/overview", "GET") as object), services: [], attention: [], pending_actions: 0 } }));
    renderCockpit();
    const empty = await screen.findByTestId("cockpit-no-hosts");
    expect(within(empty).getByRole("link", { name: "Server hinzufügen" })).toBeInTheDocument();
    expect(within(empty).queryByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeNull();
  });

  it("ohne Recht hosts.write: derselbe Leerzustand, aber ohne Knopf", async () => {
    login(["hosts.read"], false);
    vi.stubGlobal("fetch", mockFetch({ "/hosts": [], "/overview": { ...(respond("/api/v1/overview", "GET") as object), services: [], attention: [], pending_actions: 0 } }));
    renderCockpit();
    const empty = await screen.findByTestId("cockpit-no-hosts");
    expect(empty.textContent).toContain("Administrator");
    expect(within(empty).queryByRole("link")).toBeNull();
  });

  it("Erste Schritte oben im Cockpit, solange etwas offen ist; weg, wenn alles erledigt ist", async () => {
    previewState.start = true; // ein Server ohne Zugang, Module brauchen Einrichtung
    vi.stubGlobal("fetch", mockFetch());
    const first = renderCockpit();
    const card = await screen.findByTestId("first-steps");
    expect(within(card).getByTestId("first-step-server")).toHaveAttribute("data-done", "true");
    expect(within(card).getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts/h-pi");
    first.unmount();

    previewState.start = false; // der Normalfall der Vorschau: alles erledigt
    renderCockpit();
    await screen.findByTestId("cockpit-status");
    await waitFor(() => expect(screen.getByTestId("stat-Server").textContent).toContain("9/9"));
    expect(screen.queryByTestId("first-steps")).toBeNull();
  });

  it("ohne Recht auf Server und Module: keine Schritte dafür (und keine Abfragen dazu)", async () => {
    login(["notifications.read"], false);
    const fetchMock = mockFetch({ "/dashboard/layouts": [{ id: "l1", name: "Standard", is_default: true, items: [], created_at: "", updated_at: "" }] });
    vi.stubGlobal("fetch", fetchMock);
    renderCockpit();
    expect(await screen.findByTestId("modules")).toBeInTheDocument();
    const card = await screen.findByTestId("first-steps");
    expect(within(card).queryByTestId("first-step-server")).toBeNull();
    expect(within(card).queryByTestId("first-step-modules")).toBeNull();
    const urls = fetchMock.mock.calls.map(([u]) => String(u));
    expect(urls.some((u) => u.endsWith("/hosts") || u.endsWith("/extensions"))).toBe(false);
  });

  describe("eigene Apps („+ App hinzufügen“)", () => {
    it("zeigt eigene Apps zuerst, danach die erkannten Dienste, dazu Gruppen-Knöpfe", async () => {
      vi.stubGlobal("fetch", mockFetch());
      renderCockpit();
      const apps = await screen.findByTestId("apps");
      const ids = Array.from(apps.children).map((el) => el.getAttribute("data-testid"));
      expect(ids.slice(0, 6)).toEqual(["app-custom-ca1", "app-custom-ca2", "app-custom-ca3", "app-custom-ca4", "app-custom-ca5", "app-custom-ca6"]);
      expect(ids).toContain("app-docker:grafana");
      expect(ids.indexOf("app-docker:grafana")).toBeGreaterThan(5);
      // Die Kennzahl „Dienste“ zählt weiter nur erkannte Container, keine Links.
      await waitFor(() => expect(screen.getByTestId("stat-Dienste").textContent).toContain("13/14"));
      const groups = within(screen.getByTestId("app-groups")).getAllByRole("button").map((b) => b.textContent);
      expect(groups).toEqual(["Alle 16", "Netzwerk 2", "Speicher 1", "Smart Home 1", "Überwachung 1", "Ohne Gruppe 1", "Erkannt 10"]);
      fireEvent.click(within(screen.getByTestId("app-groups")).getByRole("button", { name: /^Netzwerk/ }));
      expect(Array.from(screen.getByTestId("apps").children).map((el) => el.getAttribute("data-testid"))).toEqual(["app-custom-ca1", "app-custom-ca2"]);
    });

    it("mit apps.write: Knopf „App hinzufügen“ legt eine App an, sie erscheint sofort in der Liste", async () => {
      const fetchMock = mockFetch();
      vi.stubGlobal("fetch", fetchMock);
      renderCockpit();
      await screen.findByTestId("apps");
      fireEvent.click(screen.getByRole("button", { name: /App hinzufügen/ }));
      const dialog = await screen.findByRole("dialog", { name: "App hinzufügen" });
      fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Proxmox" } });
      fireEvent.change(within(dialog).getByLabelText("Adresse"), { target: { value: "192.168.1.23:8006" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Server" }));
      fireEvent.change(within(dialog).getByLabelText(/Gruppe/), { target: { value: "Infrastruktur" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Hinzufügen" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
      expect(String(post?.[0])).toBe("/api/v1/apps");
      expect(JSON.parse(String((post?.[1] as RequestInit).body))).toMatchObject({ name: "Proxmox", url: "http://192.168.1.23:8006", icon: "server", group: "Infrastruktur" });
      const tile = await within(screen.getByTestId("apps")).findByText("Proxmox");
      expect(tile.closest("a")).toHaveAttribute("href", "http://192.168.1.23:8006");
      expect(tile.closest("a")).toHaveAttribute("rel", "noopener noreferrer");
      expect(within(screen.getByTestId("app-groups")).getByRole("button", { name: /^Infrastruktur/ })).toBeInTheDocument();
    });

    it("mit apps.write: Bearbeiten ändert den Namen, Löschen entfernt die Kachel", async () => {
      vi.stubGlobal("fetch", mockFetch());
      renderCockpit();
      await screen.findByTestId("app-custom-ca6");
      fireEvent.click(screen.getByRole("button", { name: "Menü für Drucker" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /Bearbeiten/ }));
      const dialog = await screen.findByRole("dialog", { name: "App bearbeiten" });
      fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Etikettendrucker" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Speichern" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(await screen.findByText("Etikettendrucker")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Menü für Etikettendrucker" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /Löschen/ }));
      await waitFor(() => expect(screen.queryByTestId("app-custom-ca6")).toBeNull());
      expect(confirmDialog).toHaveBeenCalledTimes(1);
    });

    it("ohne apps.write: Kacheln ja, aber kein Knopf, kein Menü", async () => {
      login(["hosts.read"], false);
      vi.stubGlobal("fetch", mockFetch());
      renderCockpit();
      expect(await screen.findByTestId("app-custom-ca1")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /App hinzufügen/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /^Menü für / })).toBeNull();
    });

    it("keine Apps und kein Recht: der Bereich fehlt ganz", async () => {
      login(["hosts.read"], false);
      previewState.noApps = true;
      vi.stubGlobal("fetch", mockFetch());
      renderCockpit();
      await waitFor(() => expect(screen.getByTestId("stat-Server").textContent).toContain("9/9"));
      await screen.findByTestId("host-cards");
      expect(screen.queryByTestId("apps-section")).toBeNull();
    });

    it("keine Apps, aber das Recht: Leerzustand mit Knopf, der den Dialog öffnet", async () => {
      previewState.noApps = true;
      vi.stubGlobal("fetch", mockFetch());
      renderCockpit();
      const empty = await screen.findByTestId("apps-empty");
      expect(empty.textContent).toContain("Noch keine Apps");
      fireEvent.click(within(empty).getByRole("button", { name: /App hinzufügen/ }));
      expect(await screen.findByRole("dialog", { name: "App hinzufügen" })).toBeInTheDocument();
    });

    it("ohne hosts.read: keine Apps und keine Abfrage dazu", async () => {
      login(["notifications.read", "apps.write"], false);
      const fetchMock = mockFetch();
      vi.stubGlobal("fetch", fetchMock);
      renderCockpit();
      expect(await screen.findByTestId("modules")).toBeInTheDocument();
      expect(screen.queryByTestId("apps-section")).toBeNull();
      expect(fetchMock.mock.calls.map(([u]) => String(u)).some((u) => u.includes("/overview") || u.includes("/apps"))).toBe(false);
    });
  });
});
