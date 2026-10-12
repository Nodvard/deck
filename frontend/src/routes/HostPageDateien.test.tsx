/**
 * Server-Seite: Knopf „Dateien“, wichtige Orte als Links und der Satz, warum es ohne Dateiquelle keine Dateien gibt
 * (`hostFiles.tsx`).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { HostPage } from "./HostPage";

vi.mock("../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

const SOURCE = (hostId: string) => ({ source_id: `ssh-sftp:${hostId}`, label: `SSH (SFTP): ${hostId}`, icon: "server", caps: {}, host_id: hostId });
const PLACES = [
  { label: "Docker: shop", path: "/srv/shop", icon: "boxes", hint: "Ordner des Compose-Projekts „shop“" },
  { label: "Protokolle", path: "/var/log", icon: "file-text", hint: null },
];
const EXTENSIONS = (state: string) => [{ id: "terminal", name: "Terminal", state, version: "1.0.0" }];

let calls: string[];

/** Wie die echte API, solange nichts anderes gesagt wird: die Daten der Design-Vorschau; `overrides` gewinnt je Pfad. */
function mockFetch(overrides: Record<string, { body?: unknown; status?: number }> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    calls.push(key);
    const override = overrides[key.split("?")[0]];
    const body = override ? override.body : respond(path, init?.method ?? "GET");
    if (body === undefined) return new Response(JSON.stringify({ detail: "nicht da" }), { status: 404 });
    return new Response(JSON.stringify(body), { status: override?.status ?? 200 });
  }));
}

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: permissions.includes("*"), locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

function renderHost(hostId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/hosts/${hostId}`]}>
        <Routes>
          <Route path="/hosts/:hostId" element={<HostPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  calls = [];
  login(["*"]);
});

describe("Server-Seite: Dateien", () => {
  it("mit Dateiquelle: Knopf „Dateien“ zum Dateimanager dieses Servers", async () => {
    mockFetch();
    renderHost("g-docker");
    await screen.findByRole("heading", { level: 1, name: /docker/ });
    const link = await screen.findByRole("link", { name: "Dateien" });
    expect(link).toHaveAttribute("href", "/files?host=g-docker");
    expect(screen.queryByTestId("host-files-hint")).toBeNull();
  });

  it("zeigt die ersten wichtigen Orte als Links in den Dateimanager", async () => {
    mockFetch({ "/files/places": { body: PLACES } });
    renderHost("g-docker");
    const block = await screen.findByTestId("host-files-places");
    const links = within(block).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["Docker: shop", "Protokolle"]);
    expect(links[0]).toHaveAttribute("href", "/files?host=g-docker&path=%2Fsrv%2Fshop");
    expect(links[1]).toHaveAttribute("href", "/files?host=g-docker&path=%2Fvar%2Flog");
    expect(links[0]).toHaveAttribute("title", "Ordner des Compose-Projekts „shop“ (/srv/shop)");
    expect(links[1]).toHaveAttribute("title", "/var/log");
    expect(calls.filter((c) => c.startsWith("/files/places"))).toEqual(["/files/places?host_id=g-docker"]);
  });

  it("zeigt höchstens vier Orte", async () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ label: `Ort ${i}`, path: `/srv/${i}`, icon: "folder", hint: null }));
    mockFetch({ "/files/places": { body: many } });
    renderHost("g-docker");
    const block = await screen.findByTestId("host-files-places");
    expect(within(block).getAllByRole("link").map((a) => a.textContent)).toEqual(["Ort 0", "Ort 1", "Ort 2", "Ort 3"]);
  });

  it("keine Orte oder ein Fehler dabei: nur der Knopf, kein Block", async () => {
    mockFetch({ "/files/places": { body: { detail: "kaputt" }, status: 500 } });
    renderHost("g-docker");
    expect(await screen.findByRole("link", { name: "Dateien" })).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.startsWith("/files/places"))).toBe(true));
    expect(screen.queryByTestId("host-files-places")).toBeNull();
    expect(screen.queryByTestId("host-files-hint")).toBeNull();
  });

  it("die Quellen werden einmal abgefragt, egal wie oft die Seite sie braucht", async () => {
    mockFetch();
    renderHost("g-docker");
    await screen.findByRole("link", { name: "Dateien" });
    expect(calls.filter((c) => c === "/files/sources")).toHaveLength(1);
  });

  it("ohne Recht files.read: kein Knopf, kein Satz, keine Abfrage", async () => {
    login(["hosts.read", "hosts.execute"]);
    mockFetch();
    renderHost("g-docker");
    await screen.findByRole("heading", { level: 1, name: /docker/ });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByRole("link", { name: "Dateien" })).toBeNull();
    expect(screen.queryByTestId("host-files-hint")).toBeNull();
    expect(calls.some((c) => c.startsWith("/files/"))).toBe(false);
  });

  it("Fehler bei den Quellen: weder Knopf noch Satz (kein Raten)", async () => {
    mockFetch({ "/files/sources": { body: { detail: "kaputt" }, status: 500 } });
    renderHost("g-docker");
    await screen.findByRole("heading", { level: 1, name: /docker/ });
    await waitFor(() => expect(calls).toContain("/files/sources"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByRole("link", { name: "Dateien" })).toBeNull();
    expect(screen.queryByTestId("host-files-hint")).toBeNull();
  });
});

describe("Server-Seite: Dateien ohne Dateiquelle", () => {
  it("Server ohne SSH-Zugang: Satz mit Link zur Einrichtung, kein toter Knopf", async () => {
    mockFetch({ "/files/sources": { body: [SOURCE("g-docker")] }, "/extensions": { body: EXTENSIONS("enabled") } });
    renderHost("g-ki");
    const hint = await screen.findByTestId("host-files-hint");
    expect(hint.textContent).toContain("Dafür braucht dieser Server einen SSH-Zugang.");
    expect(within(hint).getByRole("link", { name: "SSH-Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts/g-ki");
    expect(screen.queryByRole("link", { name: "Dateien" })).toBeNull();
    expect(calls.some((c) => c.startsWith("/files/places"))).toBe(false);
  });

  it("ohne Recht, Server zu ändern, steht der Satz ohne Link", async () => {
    login(["hosts.read", "hosts.execute", "files.read", "extensions.manage"]);
    mockFetch({ "/files/sources": { body: [] }, "/extensions": { body: EXTENSIONS("enabled") } });
    renderHost("g-ki");
    const hint = await screen.findByTestId("host-files-hint");
    expect(hint.textContent).toContain("SSH-Zugang");
    expect(within(hint).queryByRole("link")).toBeNull();
  });

  it("SSH-Zugang da, Modul „Terminal“ aus: Satz mit Link zu den Modulen", async () => {
    mockFetch({ "/files/sources": { body: [] }, "/extensions": { body: EXTENSIONS("disabled") } });
    renderHost("g-docker");
    const hint = await screen.findByTestId("host-files-hint");
    expect(hint.textContent).toContain("Modul „Terminal“");
    expect(hint.textContent).toContain("ausgeschaltet oder gestört");
    expect(within(hint).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
    expect(screen.queryByRole("link", { name: "Dateien" })).toBeNull();
  });

  it("Modul aus, aber kein Recht für Module: Satz ohne Link", async () => {
    login(["hosts.read", "hosts.execute", "files.read"]);
    mockFetch({ "/files/sources": { body: [] }, "/extensions": { body: EXTENSIONS("disabled") } });
    renderHost("g-docker");
    const hint = await screen.findByTestId("host-files-hint");
    expect(hint.textContent).toContain("Modul „Terminal“");
    expect(within(hint).queryByRole("link")).toBeNull();
  });

  it("ohne Recht für Befehle auf Servern: Satz dazu, ohne Link", async () => {
    login(["hosts.read", "files.read", "hosts.write", "extensions.manage"]);
    mockFetch({ "/files/sources": { body: [] } });
    renderHost("g-docker");
    const hint = await screen.findByTestId("host-files-hint");
    expect(hint.textContent).toBe("Dateien auf Servern siehst du nur, wenn du auch Befehle auf Servern ausführen darfst.");
  });

  it("alles in Ordnung und trotzdem keine Quelle: allgemeiner Satz", async () => {
    mockFetch({ "/files/sources": { body: [SOURCE("h-pi")] }, "/extensions": { body: EXTENSIONS("enabled") } });
    renderHost("g-docker");
    const hint = await screen.findByTestId("host-files-hint");
    expect(hint.textContent).toBe("Für diesen Server gibt es gerade keine Dateiquelle.");
  });

  it("die Quelle eines anderen Servers zählt nicht", async () => {
    mockFetch({ "/files/sources": { body: [SOURCE("h-pi")] }, "/extensions": { body: EXTENSIONS("enabled") } });
    renderHost("g-docker");
    await screen.findByTestId("host-files-hint");
    expect(screen.queryByRole("link", { name: "Dateien" })).toBeNull();
  });
});
