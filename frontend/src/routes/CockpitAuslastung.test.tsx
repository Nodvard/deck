/**
 * Cockpit „Infrastruktur“: Auslastungs-Karten für Linux-Server.
 * Die Werte kommen in EINER Abfrage (`GET /hosts/metrics/latest`) aus dem Verlauf des Kerns, nie per SSH.
 * Daten aus dem Vorschau-Szenario „ohne Proxmox“; eigene Datei, weil das Szenario die Testdaten umstellt.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { applyOhneProxmox, previewState, respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { Cockpit } from "./Cockpit";

type Latest = { hosts: Record<string, Record<string, unknown>>; stale_after_s: number };

function mockFetch(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    const body = key in overrides ? overrides[key] : respond(path, init?.method ?? "GET");
    if (body === undefined) return new Response(JSON.stringify({ detail: "nicht da" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

function paths(fetchMock: ReturnType<typeof mockFetch>): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input).replace(/^https?:\/\/[^/]+/, "").replace(/^\/api\/v1/, ""));
}

function renderCockpit() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><Cockpit /></MemoryRouter>
    </QueryClientProvider>,
  );
}

function login(permissions: string[] = ["*"]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: permissions.includes("*"), locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

const latestOf = () => respond("/api/v1/hosts/metrics/latest", "GET") as Latest;

beforeEach(() => {
  login();
  previewState.ohneProxmoxMin = false;
  previewState.firstStepsDismissed = true;
});

describe("Cockpit: Auslastung der Linux-Server", () => {
  beforeAll(() => applyOhneProxmox(false));

  it("Server mit Messwerten: Karte mit CPU, RAM und Platte samt Zahlen", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderCockpit();
    const nas = await screen.findByTestId("server-m-nas");
    expect(within(nas).getByRole("img", { name: "CPU 1 %" })).toBeInTheDocument();
    expect(within(nas).getByRole("img", { name: "RAM 18 %" })).toBeInTheDocument();
    expect(within(nas).getByRole("img", { name: "Platte 78 %" })).toBeInTheDocument();
    expect(nas.textContent).toContain("717 MB / 3.9 GB");
    expect(nas.textContent).toContain("1.5 TB / 2.0 TB");
    expect(nas.textContent).toContain("läuft seit 62 T");
    expect(nas).toHaveAttribute("data-state", "live");
    expect(screen.queryByTestId("server-m-nas-note")).toBeNull();
    expect(within(nas).getByRole("link", { name: "NAS" })).toHaveAttribute("href", "/hosts/m-nas");
    // Der Windows-PC hat keine Messung: schlichte Zeile, kein Hinweis zum Modul (das hilft ihm nicht).
    expect(screen.getByTestId("machine-m-win")).toBeInTheDocument();
    expect(screen.queryByTestId("machine-m-win-hint")).toBeNull();
  });

  it("holt die Werte aller Server in EINER Abfrage und fragt keinen Server einzeln", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCockpit();
    await screen.findByTestId("server-m-pi");
    const calls = paths(fetchMock);
    expect(calls.filter((c) => c === "/hosts/metrics/latest")).toHaveLength(1);
    expect(calls.filter((c) => /^\/hosts\/[^/]+\/metrics$/.test(c))).toEqual([]);
  });

  it("zu alter Wert: Karte gedämpft und „veraltet“ mit Alter", async () => {
    const latest = latestOf();
    latest.hosts["m-nas"] = { ...latest.hosts["m-nas"], stale: true, age_s: 300 };
    vi.stubGlobal("fetch", mockFetch({ "/hosts/metrics/latest": latest }));
    renderCockpit();
    const nas = await screen.findByTestId("server-m-nas");
    expect(nas).toHaveAttribute("data-state", "stale");
    expect(screen.getByTestId("server-m-nas-note").textContent).toBe("veraltet · letzte Messung vor 5 min");
    expect(within(nas).getByRole("img", { name: "Platte 78 %" })).toBeInTheDocument(); // Zahl bleibt sichtbar, ist aber als veraltet gekennzeichnet
    expect(screen.getByTestId("server-m-pi")).toHaveAttribute("data-state", "live");
  });

  it("nicht erreichbarer Server: keine alten Werte als aktuell, dafür der Grund", async () => {
    const hosts = respond("/api/v1/hosts", "GET") as { id: string }[];
    vi.stubGlobal("fetch", mockFetch({ "/hosts": hosts.map((h) => (h.id === "m-nas" ? { ...h, status: "down" } : h)) }));
    renderCockpit();
    const nas = await screen.findByTestId("server-m-nas");
    expect(nas).toHaveAttribute("data-state", "offline");
    expect(within(nas).getByRole("img", { name: "CPU unbekannt" })).toBeInTheDocument();
    expect(within(nas).getByRole("img", { name: "RAM unbekannt" })).toBeInTheDocument();
    expect(within(nas).getByRole("img", { name: "Platte unbekannt" })).toBeInTheDocument();
    expect(nas.textContent).not.toContain("GB");
    expect(nas.textContent).not.toContain("läuft seit");
    expect(screen.getByTestId("server-m-nas-note").textContent).toContain("nicht erreichbar");
    expect(screen.getByTestId("attention").textContent).toContain("NAS ist nicht erreichbar");
  });

  it("Server ohne Messwerte bleiben eine Zeile, mit Hinweis wenn das Modul System läuft", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/hosts/metrics/latest": { hosts: {}, stale_after_s: 120 } }));
    renderCockpit();
    const deb = await screen.findByTestId("machine-m-deb");
    expect(screen.queryByTestId("host-cards")).toBeNull();
    expect(await screen.findByTestId("machine-m-deb-hint")).toHaveTextContent("Noch keine Messwerte");
    expect(deb.textContent).toContain("192.168.2.40");
  });

  it("ohne das Modul System sagt die Zeile warum", async () => {
    // Modul aus: keine Seite „System“ im Katalog und keine Werte.
    const pages = (respond("/api/v1/pages", "GET") as { ext_id: string }[]).filter((p) => p.ext_id !== "system");
    vi.stubGlobal("fetch", mockFetch({ "/pages": pages, "/hosts/metrics/latest": { hosts: {}, stale_after_s: 120 } }));
    renderCockpit();
    expect(await screen.findByTestId("machine-m-deb-hint")).toHaveTextContent("Modul „System“ ist aus");
    expect(screen.queryByTestId("host-cards")).toBeNull();
  });

  it("ohne SSH-Zugang oder bei abgeschalteten Messwerten steht der Grund in der Zeile", async () => {
    const hosts = respond("/api/v1/hosts", "GET") as { id: string }[];
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/metrics/latest": { hosts: {}, stale_after_s: 120 },
      "/hosts": hosts.map((h) => (h.id === "m-nas" ? { ...h, credential: null } : h.id === "m-deb" ? { ...h, enabled: false } : h)),
    }));
    renderCockpit();
    expect(await screen.findByTestId("machine-m-nas-hint")).toHaveTextContent("noch kein SSH-Zugang");
    expect(screen.getByTestId("machine-m-deb-hint")).toHaveTextContent("Messwerte für diesen Server sind aus");
  });

  it("ein Server, der nie geantwortet hat, schickt zuerst zum Zugang statt auf Messwerte zu warten", async () => {
    const hosts = respond("/api/v1/hosts", "GET") as { id: string }[];
    vi.stubGlobal("fetch", mockFetch({
      "/hosts/metrics/latest": { hosts: {}, stale_after_s: 120 },
      "/hosts": hosts.map((h) => (h.id === "m-deb" ? { ...h, status: "down", last_seen_at: null } : h)),
    }));
    renderCockpit();
    expect(await screen.findByTestId("machine-m-deb-hint")).toHaveTextContent("Noch keine Verbindung: prüfe zuerst den Zugang");
  });

  it("fällt die Abfrage der Werte aus, bleibt das Cockpit benutzbar: alle Server als Zeilen", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/hosts/metrics/latest": undefined }));
    renderCockpit();
    expect(await screen.findByTestId("machine-m-pi")).toBeInTheDocument();
    expect(screen.queryByTestId("host-cards")).toBeNull();
  });

  it("ohne Recht auf Server wird nichts abgefragt", async () => {
    login(["notifications.read"]);
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCockpit();
    await waitFor(() => expect(screen.getByLabelText("Cockpit")).toBeInTheDocument());
    expect(paths(fetchMock).filter((c) => c.startsWith("/hosts"))).toEqual([]);
  });
});

describe("Cockpit: Hypervisor-Knoten und Linux-Server sehen gleich aus", () => {
  beforeAll(() => {
    // Standardszenario mit Proxmox-Knoten: neu laden, indem das Szenario „ohne Proxmox“ nicht aktiv ist.
    previewState.ohneProxmox = false;
  });

  it("gleiche Karte, gleiche Ringe; nur Knoten haben (noch) keine Platte", async () => {
    // Knoten und Server in einem Raster: aus den Testdaten des Szenarios ein Knoten dazu.
    const hosts = (respond("/api/v1/hosts", "GET") as Record<string, unknown>[]).concat([
      { ...(respond("/api/v1/hosts", "GET") as Record<string, unknown>[])[0], id: "n-pve9", display_name: "pve9", kind: "hypervisor", status: "up", provider_ext_id: "proxmox" },
    ]);
    vi.stubGlobal("fetch", mockFetch({
      "/hosts": hosts,
      "/hosts/n-pve9/metrics": { values: { cpu_percent: 14.2, mem_used_bytes: 11.1 * 1024 ** 3, mem_total_bytes: 13.4 * 1024 ** 3, uptime_s: 38305 }, sampled_at: "" },
    }));
    renderCockpit();
    const node = await screen.findByTestId("node-n-pve9");
    const server = await screen.findByTestId("server-m-pi");
    await waitFor(() => expect(within(node).getByRole("img", { name: "CPU 14 %" })).toBeInTheDocument());
    expect(node.className).toBe(server.className);
    expect(node.parentElement).toBe(server.parentElement);
    expect(within(node).getByRole("img", { name: "RAM 83 %" })).toBeInTheDocument();
    expect(within(node).queryByRole("img", { name: /Platte/ })).toBeNull();
    expect(within(server).getByRole("img", { name: /Platte/ })).toBeInTheDocument();
  });
});

describe("Cockpit: 30 Server", () => {
  beforeAll(() => applyOhneProxmox(false, true));

  it("alle Karten in einem Raster, Windows und Geräte ohne Messung als Zeilen, weiterhin eine Abfrage", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderCockpit();
    await screen.findByTestId("server-x-01");
    const grid = screen.getByTestId("host-cards");
    const cards = within(grid).getAllByTestId(/^server-x?-?[a-z0-9-]+$/).filter((el) => el.hasAttribute("data-state"));
    // 3 (Pi, VM, NAS) + 28 neue Linux-Server ohne die 3 Windows-Rechner
    expect(cards.length).toBe(3 + 28 - 3);
    const states = new Set(cards.map((c) => c.getAttribute("data-state")));
    expect(states).toEqual(new Set(["live", "stale", "offline"]));
    expect(document.querySelectorAll('[data-testid^="machine-"][data-testid$="-hint"]').length).toBe(0);
    expect(document.querySelectorAll('[data-testid^="machine-x-"]').length).toBe(3);
    expect(paths(fetchMock).filter((c) => c === "/hosts/metrics/latest")).toHaveLength(1);
    expect(paths(fetchMock).filter((c) => /^\/hosts\/[^/]+\/metrics$/.test(c))).toEqual([]);
  });
});
