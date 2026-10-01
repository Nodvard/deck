/**
 * Der Test „ohne Proxmox“ (Pflicht vor jeder Veröffentlichung): eine Installation
 * mit nur von Hand angelegten Servern, Proxmox und Backups aus. Die Daten kommen aus dem Vorschau-Szenario
 * `?scenario=ohne-proxmox`. Eigene Datei, weil das Szenario die gemeinsamen Testdaten umstellt.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { applyOhneProxmox, previewState, respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { Cockpit } from "./Cockpit";
import { HostPage } from "./HostPage";

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

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderCockpit() {
  return render(
    <QueryClientProvider client={client()}>
      <MemoryRouter><Cockpit /></MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderHost(hostId: string) {
  return render(
    <QueryClientProvider client={client()}>
      <MemoryRouter initialEntries={[`/hosts/${hostId}`]}>
        <Routes><Route path="/hosts/:hostId" element={<HostPage />} /></Routes>
      </MemoryRouter>
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

beforeAll(() => applyOhneProxmox(false));
beforeEach(() => {
  login();
  previewState.ohneProxmoxMin = false;
  previewState.firstStepsDismissed = true;
});

describe("Cockpit ohne Proxmox", () => {
  it("von Hand angelegte Server: keine Knoten-Karten, keine Backup-Kachel, und „noch nicht geprüft“ statt „alles läuft rund“", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderCockpit();
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Keine Störung bekannt"));
    const status = screen.getByTestId("cockpit-status").textContent ?? "";
    expect(status).toContain("1 von 4 Hosts online, 3 noch nicht geprüft");
    expect(status).not.toContain("Alles läuft rund");

    const hosts = screen.getByTestId("stat-Hosts").textContent ?? "";
    expect(hosts).toContain("1/4");
    expect(hosts).toContain("3 noch nicht geprüft");
    expect(screen.getByTestId("stat-Dienste").textContent).toContain("5/5");
    expect(screen.queryByTestId("stat-Backups")).toBeNull();

    // Linux-Server mit Messwerten (System per SSH) bekommen eine Karte, der Windows-PC bleibt eine Zeile.
    for (const id of ["m-pi", "m-deb", "m-nas"]) expect(await screen.findByTestId(`server-${id}`)).toBeInTheDocument();
    expect(await screen.findByTestId("machine-m-win")).toBeInTheDocument();
    expect(document.querySelector('[data-testid^="node-"]')).toBeNull();
    // Der Zustand steht auch im Tooltip des Punktes, auf Deutsch.
    expect(within(screen.getByTestId("server-m-deb")).getByTitle("noch nicht geprüft")).toBeInTheDocument();
    expect(screen.getByTestId("attention").textContent).not.toMatch(/Proxmox|Backup/);
  });

  it("ein Server ist wirklich nicht erreichbar: das ist ein Problem, kein „noch nicht geprüft“", async () => {
    const hosts = respond("/api/v1/hosts", "GET") as { id: string; status: string }[];
    vi.stubGlobal("fetch", mockFetch({ "/hosts": hosts.map((h) => (h.id === "m-nas" ? { ...h, status: "down" } : h)) }));
    renderCockpit();
    await waitFor(() => expect(screen.getByTestId("cockpit-status").textContent).toContain("Eine Sache braucht deine Aufmerksamkeit"));
    expect(screen.getByTestId("attention").textContent).toContain("NAS ist nicht erreichbar");
  });

  it("noch keine Container erfasst: „–“ statt 0/0", async () => {
    const overview = respond("/api/v1/overview", "GET") as object;
    vi.stubGlobal("fetch", mockFetch({ "/overview": { ...overview, services: [], services_running: 0 } }));
    renderCockpit();
    await waitFor(() => expect(screen.getByTestId("stat-Dienste").textContent).toContain("noch keine erfasst"));
    const tile = screen.getByTestId("stat-Dienste").textContent ?? "";
    expect(tile).not.toContain("0/0");
    expect(tile).toContain("–");
    expect(screen.getByTestId("cockpit-status").textContent).not.toContain("Dienste aktiv");
  });
});

describe("Server-Seite ohne Proxmox", () => {
  it("Server, den noch nie jemand geprüft hat: „noch nicht geprüft“, Auslastung kommt per SSH vom Modul System", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderHost("m-deb");
    expect(await screen.findByRole("heading", { name: "Debian-VM" })).toBeInTheDocument();
    expect(screen.getByText(/192\.168\.2\.40 · noch nicht geprüft/)).toBeInTheDocument();
    expect(await screen.findByTestId("host-metrics")).toBeInTheDocument();
    expect(screen.queryByTestId("host-metrics-hint")).toBeNull();
    expect(screen.queryByTestId("host-provider-off")).toBeNull();
  });

  it("ohne das Modul System: ein Hinweis mit Knopf statt einer stillen Lücke", async () => {
    previewState.ohneProxmoxMin = true;
    vi.stubGlobal("fetch", mockFetch());
    renderHost("m-deb");
    const hint = await screen.findByTestId("host-metrics-hint");
    expect(hint.textContent).toContain("Modul „System“");
    expect(within(hint).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
    expect(screen.queryByTestId("host-metrics")).toBeNull();
  });

  it("ohne SSH-Zugang: der Hinweis führt zum Zugang; ohne Recht gibt es keinen Knopf; Windows bekommt keinen Hinweis", async () => {
    previewState.ohneProxmoxMin = true;
    const all = respond("/api/v1/hosts", "GET") as { id: string }[];
    const nas = { ...all.find((h) => h.id === "m-nas")!, credential: null };
    const win = all.find((h) => h.id === "m-win")!;
    vi.stubGlobal("fetch", mockFetch({ "/hosts/m-nas": nas, "/hosts/m-win": win }));
    const first = renderHost("m-nas");
    const hint = await screen.findByTestId("host-metrics-hint");
    expect(hint.textContent).toContain("SSH-Zugang");
    expect(within(hint).getByRole("link", { name: "SSH-Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts/m-nas");
    first.unmount();

    login(["hosts.read"]);
    renderHost("m-nas");
    const readOnly = await screen.findByTestId("host-metrics-hint");
    expect(within(readOnly).queryByRole("link")).toBeNull();
  });

  it("Windows-Server: keine Auslastung, aber auch kein Hinweis auf ein Modul, das dafür nichts tun kann", async () => {
    previewState.ohneProxmoxMin = true;
    vi.stubGlobal("fetch", mockFetch());
    renderHost("m-win");
    expect(await screen.findByRole("heading", { name: "Spiele-PC" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Werkzeuge & Einstellungen/)).toBeInTheDocument());
    expect(screen.queryByTestId("host-metrics-hint")).toBeNull();
    expect(screen.queryByTestId("host-metrics")).toBeNull();
  });

  it("von Proxmox eingelesener Server, Modul inzwischen aus: Hinweis, dass nichts mehr nachgeführt wird", async () => {
    const stale = { ...(respond("/api/v1/hosts/m-pi", "GET") as object), id: "vm-alt", display_name: "alte-vm", kind: "vm", provider_ext_id: "proxmox", status: "up", last_seen_at: "2026-09-01T10:00:00Z" };
    const extensions = (respond("/api/v1/extensions", "GET") as object[]);
    vi.stubGlobal("fetch", mockFetch({ "/hosts/vm-alt": stale, "/extensions": extensions }));
    renderHost("vm-alt");
    const banner = await screen.findByTestId("host-provider-off");
    expect(banner.textContent).toContain("Dieser Server wurde vom Modul „Proxmox VE“ eingelesen");
    expect(banner.textContent).toContain("nicht mehr nachgeführt");
  });
});
