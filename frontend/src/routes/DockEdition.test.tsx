import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { CommandPalette } from "../components/CommandPalette";
import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { DECK_EDITION, useEditionStore } from "../state/edition";
import { AppShell } from "./AppShell";
import { SettingsLayout } from "./settings/SettingsLayout";
import { HostsSettings } from "./settings/HostsSettings";
import { HostForm } from "./settings/hosts/HostForm";
import { AutomationSettings } from "./settings/AutomationSettings";
import { TerminalPage } from "./TerminalPage";
import { AuditSettings } from "./settings/AuditSettings";
import { AboutSettings } from "./settings/AboutSettings";
import { AppearanceSettings } from "./settings/AppearanceSettings";
import { TrustedProxyCard } from "./settings/TrustedProxyCard";
import { VaultSettings } from "./settings/VaultSettings";
import { guideUrl } from "../lib/docs";
import { DesignNotice } from "../components/DesignNotice";
import { routes } from "./router";

vi.mock("../lib/ws", () => ({ wsClient: { ensureConnected: vi.fn(), disconnect: vi.fn() }, useWsSubscription: vi.fn() }));

beforeEach(() => {
  useEditionStore.setState({ edition: { ...DECK_EDITION, id: "dock", product_name: "Nodvard Dock", choose_modules: false, repo_tools: false }, loaded: true });
  useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "owner", display_name: "Inhaber", email: null, is_owner: true, locale: "de", permissions: ["*"] } });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    const data = ["/api/v1/hosts", "/api/v1/secrets", "/api/v1/terminal/hosts", "/api/v1/console/hosts", "/api/v1/host-groups"].includes(path) ? []
      : path === "/api/v1/pages" ? [{ ext_id: "docker", id: "matrix", path: "/matrix", title: "Docker", nav_order: 30, show_in_nav: true }]
      : respond(path, init?.method ?? "GET") ?? {};
    return new Response(JSON.stringify(data), { status: 200 });
  }));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useEditionStore.setState({ edition: DECK_EDITION, loaded: false }); });

function show(element: JSX.Element, path = "/settings/hosts") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{element}</MemoryRouter></QueryClientProvider>) };
}

it("Dock zeigt im Menü nur seine Arbeitsbereiche", async () => {
  show(<AppShell />);
  expect(await screen.findByRole("link", { name: "Docker" })).toBeInTheDocument();
  for (const name of ["Apps", "Dateien", "Terminal", "Übersicht"]) expect(screen.queryByRole("link", { name })).toBeNull();
  expect(screen.getByText("Docker-Verwaltung")).toBeInTheDocument();
});

it("Dock zeigt feste Module und seinen eigenen Namen in den Einstellungen", () => {
  show(<SettingsLayout />);
  expect(screen.queryByText("Module ein- und ausschalten")).toBeNull();
  expect(screen.getByRole("link", { name: /Docker.*Einstellungen/ })).toHaveAttribute("href", "/settings/extensions/docker");
  expect(screen.getByText("Über Nodvard Dock")).toBeInTheDocument();
});

it("Dock bietet in der Suche keine allgemeinen Deck-Seiten an", async () => {
  show(<CommandPalette open onOpenChange={() => undefined} />);
  await screen.findByRole("option", { name: "Docker" });
  for (const name of ["Apps", "Dateien", "Terminal", "Übersicht", "Einstellungen: Erweiterungen"])
    expect(screen.queryByRole("option", { name })).toBeNull();
});

it("Dock empfiehlt in der Serverliste keine fremden Module", async () => {
  show(<HostsSettings />);
  await screen.findByText("Noch keine Server.");
  expect(screen.getByText(/Dafür reichen Name, Adresse/)).toBeInTheDocument();
  expect(screen.queryByText(/Proxmox einrichten/)).toBeNull();
  expect(screen.queryByText(/Proxmox/)).toBeNull();
  expect(screen.queryByRole("button", { name: /Beispieldaten/ })).toBeNull();
  expect(screen.queryAllByText(/Nodvard Deck/)).toHaveLength(0);
});

it("Dock bietet am Server keine fremden Modulschalter an", async () => {
  const host = { id: "h1", name: "containers", display_name: "Container", address: "192.0.2.10", os_family: "linux", tags: ["docker"], managed_tags: [] };
  show(<HostForm host={host as never} onSaved={() => undefined} />);
  if (screen.queryByText("Erweitert")) fireEvent.click(screen.getByText("Erweitert"));
  expect(screen.queryByRole("switch", { name: /Shield|Messwerte|sudo/ })).toBeNull();
  expect(screen.queryByText(/gameserver|Container-Wache/)).toBeNull();
});

it("Dock erklärt die Freigabe für Container-Aktionen", async () => {
  show(<AutomationSettings />);
  await waitFor(() => expect(screen.queryByText(/Wird geladen|Lade/)).toBeNull());
  expect(await screen.findByText("Gilt für Änderungen an Containern und Stacks." )).toBeInTheDocument();
});

it("Dock zeigt beim Container-Terminal keine allgemeine Serverauswahl", async () => {
  show(<TerminalPage />, "/terminal");
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(screen.queryByText(/Modul „Terminal“|SSH-Zugang einrichten/)).toBeNull();
  expect(await screen.findByRole("link", { name: "Docker öffnen" })).toHaveAttribute("href", "/ext/docker/matrix");
});

it("Dock nennt sich auf der Über-Seite und verlinkt seine Hilfe", async () => {
  show(<AboutSettings />);
  expect(screen.getByRole("heading", { name: "Über Nodvard Dock" })).toBeInTheDocument();
  expect(screen.queryAllByText(/Nodvard Deck/)).toHaveLength(0);
});

it.each([
  "/files", "/apps", "/hosts/h1", "/console/h1", "/ext/shield/soc",
  "/ext/documents/documents", "/ext/inventory/inventory", "/settings/extensions/proxmox",
])("Dock sperrt den direkten Seitenaufruf %s", async (path) => {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  expect(await screen.findByTestId("not-found")).toBeInTheDocument();
  router.dispose();
});

it("Dock öffnet Docker beim Start und dessen Einstellungen beim alten Modul-Link", async () => {
  for (const [from, to] of [["/", "/ext/docker/matrix"], ["/settings/extensions", "/settings/extensions/docker"]]) {
    const router = createMemoryRouter(routes, { initialEntries: [from] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    await waitFor(() => expect(router.state.location.pathname).toBe(to));
    view.unmount();
    router.dispose();
  }
});

it("Dock verlinkt seine eigene Anleitung", () => {
  expect(guideUrl("servers")).toBe("https://github.com/nodvard/deck/blob/main/deploy/nodvard-dock/README.md#3-server-verbinden-auch-den-eigenen");
  useEditionStore.setState({ edition: DECK_EDITION });
  expect(guideUrl("servers")).toContain("docs/11-ERST-EINRICHTUNG.md#3-den-ersten-server-verbinden");
});

it.each([
  ["Darstellung", <AppearanceSettings />],
  ["Tresor", <VaultSettings />],
  ["Proxy", <TrustedProxyCard settings={[]} />],
])("Dock nennt sich in den Hilfetexten für %s", async (_name, element) => {
  show(element as JSX.Element);
  expect((await screen.findAllByText(/Nodvard Dock/)).length).toBeGreaterThan(0);
  expect(screen.queryAllByText(/Nodvard Deck/)).toHaveLength(0);
});

it("Dock zeigt keinen Designhinweis der anderen Ausgabe", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ design_notice_dismissed: false })));
  const { client } = show(<DesignNotice />);
  await waitFor(() => expect(client.getQueryState(["me", "preferences"])?.status).toBe("success"));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(screen.queryByTestId("design-notice")).toBeNull();
});

it("Dock bietet im Protokoll nur vorhandene Auslöser an", async () => {
  show(<AuditSettings />);
  expect(screen.getByLabelText("Auslöser").querySelector('option[value="ai"]')).toBeNull();
});

it("Dock erklärt neue Tresoreinträge für seine Registries", async () => {
  show(<VaultSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Neuer Eintrag" }));
  expect(await screen.findByText(/Zum Beispiel ein Passwort für eine private Registry/)).toBeInTheDocument();
  expect(screen.queryByText(/ein Skript oder ein Modul braucht/)).toBeNull();
});
