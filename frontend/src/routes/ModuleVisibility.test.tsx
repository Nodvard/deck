import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { previewState, respond } from "../preview/fixtures";
import { useWsSubscription } from "../lib/ws";
import { useAuthStore } from "../state/auth";
import { DECK_EDITION, useEditionStore } from "../state/edition";
import { AppShell } from "./AppShell";
import { DashboardPage } from "./DashboardPage";
import { ExtensionsSettings } from "./settings/ExtensionsSettings";

vi.mock("../lib/ws", () => ({
  wsClient: { ensureConnected: vi.fn(), disconnect: vi.fn() },
  useWsSubscription: vi.fn(),
}));

const pages = ["erste", "zweite"].map((id, nav_order) => ({
  ext_id: "fremd", id, path: `/${id}`, title: `Fremde Seite ${id}`, icon: null,
  nav_section: "Fremdes Modul", nav_order, permissions: [], component: "X", mobile: "widgets", show_in_nav: true,
}));
const widgets = ["erste", "zweite"].map((id) => ({
  ext_id: "fremd", id, title: `Fremde Kachel ${id}`, icon: null, description: null,
  size: { w: 2, h: 2, min_w: 1, min_h: 1 }, refresh: { interval_s: null, ws_channel: null },
  data_endpoint: `widgets/${id}`, permissions: [], component: null, default_enabled: true,
  view: { kind: "stat", value: "{{ count }}", label: `Wert ${id}`, delta: null, tone: "neutral", sparkline_field: null },
}));
const items = widgets.map((widget, index) => ({
  ext_id: widget.ext_id, widget_id: widget.id, x: index * 2, y: 0, w: 2, h: 2, config: {},
}));
const layout = { id: "layout-1", name: "Standard", is_default: true, items, created_at: "", updated_at: "" };
let enabled: Record<string, boolean>;
let failDisable: boolean;
let otherFilesProvider: boolean;
let changes: string[];
let layoutWrites: unknown[];

beforeEach(() => {
  enabled = { terminal: true, fremd: true };
  failDisable = false;
  otherFilesProvider = false;
  changes = [];
  layoutWrites = [];
  useEditionStore.setState({ edition: DECK_EDITION });
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
  previewState.noHosts = false;
  previewState.start = false;
  previewState.demo = false;
  previewState.firstStepsDismissed = true;
  previewState.designNoticeDismissed = true;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    const row = (id: string) => ({
      id, name: id === "terminal" ? "Terminal" : "Fremdes Modul", version: "1.0.0", source: "bundled",
      state: enabled[id] ? "enabled" : "disabled", description: null, icon: null,
      granted_permissions: [], last_error: null,
    });
    let body: unknown;
    if (key === "/capabilities") body = {
      api_version: "0.1.0", view_types: [], extensions: Object.keys(enabled).filter((id) => enabled[id]).map(row),
      feature_flags: { terminal: enabled.terminal, files: enabled.terminal || otherFilesProvider },
    };
    else if (key === "/extensions") body = Object.keys(enabled).map(row);
    else if (key === "/pages") body = enabled.fremd ? pages : [];
    else if (key === "/widgets") body = enabled.fremd ? widgets : [];
    else if (key.startsWith("/ext/fremd/widgets/")) body = { data: { count: 3 }, meta: {} };
    else if (key === "/terminal/hosts") body = enabled.terminal ? ["h-pi"] : [];
    else if (key === "/dashboard/layouts") body = [layout];
    else if (key === "/dashboard/layouts/layout-1" && method === "PUT") {
      layoutWrites.push(JSON.parse(init!.body as string));
      body = layout;
    } else {
      const toggle = key.match(/^\/extensions\/([^/]+)\/(enable|disable)$/);
      if (toggle && method === "POST") {
        changes.push(key);
        if (failDisable && toggle[2] === "disable") return new Response(JSON.stringify({ detail: "Ausschalten fehlgeschlagen" }), { status: 500 });
        enabled[toggle[1]] = toggle[2] === "enable";
        body = row(toggle[1]);
      } else body = respond(path, method, typeof init?.body === "string" ? JSON.parse(init.body) : undefined);
    }
    if (body === undefined) throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(useWsSubscription).mockClear();
});

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/settings" element={<ExtensionsSettings />} />
            <Route path="/settings/extensions" element={<ExtensionsSettings />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

const nav = () => within(screen.getByRole("navigation", { name: "Hauptnavigation" }));
const modules = () => within(screen.getByTestId("modules"));

async function settings() {
  fireEvent.click(screen.getByRole("link", { name: "Einstellungen" }));
  await screen.findByRole("switch", { name: "Terminal ausschalten" });
}

async function disable(name: string) {
  fireEvent.click(screen.getByRole("switch", { name: `${name} ausschalten` }));
  fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
}

function publish(name: string, ext_id: string) {
  const handler = vi.mocked(useWsSubscription).mock.calls.filter(([channel]) => channel === "events").at(-1)![1];
  act(() => handler({ name, data: { ext_id } }));
}

describe("Modulsichtbarkeit ohne Neuladen", () => {
  it("zeigt Terminal und Dateien bei ausgeschaltetem Anbieter weder im Menü noch in der Übersicht", async () => {
    enabled.terminal = false;
    renderApp();
    await screen.findByText("Wert erste");
    for (const area of [nav(), modules()]) {
      expect(area.queryByRole("link", { name: "Terminal" })).toBeNull();
      expect(area.queryByRole("link", { name: "Dateien" })).toBeNull();
    }
  });

  it("entfernt Terminal und Dateien nach dem Ausschalten auch ohne WebSocket und stellt sie beim Einschalten wieder her", async () => {
    renderApp();
    await screen.findByText("Wert erste");
    expect(nav().getByRole("link", { name: "Terminal" })).toBeInTheDocument();
    expect(modules().getByRole("link", { name: "Terminal" })).toBeInTheDocument();
    await settings();
    await disable("Terminal");
    await screen.findByText("„Terminal“ ausgeschaltet.");
    await waitFor(() => expect(nav().queryByRole("link", { name: "Terminal" })).toBeNull());
    fireEvent.click(nav().getByRole("link", { name: "Übersicht" }));
    await screen.findByText("Wert erste");
    expect(modules().queryByRole("link", { name: "Terminal" })).toBeNull();
    expect(modules().queryByRole("link", { name: "Dateien" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Terminal Heimserver" })).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "Einstellungen" }));
    fireEvent.click(await screen.findByRole("switch", { name: "Terminal einschalten" }));
    await screen.findByText("„Terminal“ eingeschaltet.");
    await waitFor(() => expect(nav().getByRole("link", { name: "Terminal" })).toBeInTheDocument());
    fireEvent.click(nav().getByRole("link", { name: "Übersicht" }));
    await waitFor(() => expect(modules().getByRole("link", { name: "Terminal" })).toBeInTheDocument());
    expect(modules().getByRole("link", { name: "Dateien" })).toBeInTheDocument();
  });

  it("übernimmt Ausschalten und Einschalten aus einem anderen Tab samt Terminalzielen und allen Seiten und Kacheln", async () => {
    const client = renderApp();
    await screen.findByText("Wert erste");
    await screen.findByText("Wert zweite");
    await screen.findByRole("link", { name: "Terminal Heimserver" });
    enabled.terminal = false;
    enabled.fremd = false;
    publish("extension.disabled", "terminal");
    publish("extension.disabled", "fremd");
    await waitFor(() => {
      for (const area of [nav(), modules()]) {
        expect(area.queryByRole("link", { name: "Terminal" })).toBeNull();
        expect(area.queryByRole("link", { name: "Dateien" })).toBeNull();
        for (const page of pages) expect(area.queryByRole("link", { name: page.title })).toBeNull();
      }
      expect(screen.queryByText("Wert erste")).toBeNull();
      expect(screen.queryByText("Wert zweite")).toBeNull();
      expect(screen.queryByRole("link", { name: "Terminal Heimserver" })).toBeNull();
    });
    expect(client.getQueryData(["dashboard-layouts"])).toEqual([layout]);
    expect(layoutWrites).toEqual([]);
    enabled.terminal = true;
    enabled.fremd = true;
    publish("extension.enabled", "terminal");
    publish("extension.enabled", "fremd");
    await screen.findByText("Wert erste");
    await screen.findByText("Wert zweite");
    await waitFor(() => {
      for (const area of [nav(), modules()]) {
        expect(area.getByRole("link", { name: "Terminal" })).toBeInTheDocument();
        for (const page of pages) expect(area.getByRole("link", { name: page.title })).toBeInTheDocument();
      }
    });
    expect(layoutWrites).toEqual([]);
  });

  it("entfernt bei einem beliebigen Modul alle Seiten und Kacheln über den Schalter", async () => {
    renderApp();
    await screen.findByText("Wert erste");
    await settings();
    await disable("Fremdes Modul");
    await screen.findByText("„Fremdes Modul“ ausgeschaltet.");
    await waitFor(() => {
      for (const page of pages) expect(nav().queryByRole("link", { name: page.title })).toBeNull();
    });
    fireEvent.click(nav().getByRole("link", { name: "Übersicht" }));
    await screen.findByTestId("dashboard-no-widgets");
    for (const page of pages) expect(modules().queryByRole("link", { name: page.title })).toBeNull();
    expect(screen.queryByText("Wert erste")).toBeNull();
    expect(screen.queryByText("Wert zweite")).toBeNull();
    expect(layoutWrites).toEqual([]);
  });

  it("erneuert den inaktiven Widgetkatalog vor dem schnellen Zurückwechseln", async () => {
    const originalFetch = fetch;
    let releaseRefresh!: () => void;
    let releaseReturn!: () => void;
    const refreshing = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    const returning = new Promise<void>((resolve) => { releaseReturn = resolve; });
    let readsAfterDisable = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/widgets") && !enabled.fremd) {
        readsAfterDisable += 1;
        await (readsAfterDisable === 1 ? refreshing : returning);
      }
      return originalFetch(input, init);
    }));
    const client = renderApp();
    try {
      await screen.findByText("Wert erste");
      await settings();
      await disable("Fremdes Modul");
      await screen.findByText("„Fremdes Modul“ ausgeschaltet.");
      await waitFor(() => expect(readsAfterDisable).toBe(1));
      const toggle = await screen.findByRole("switch", { name: "Fremdes Modul einschalten" });
      expect(toggle).toBeDisabled();
      await act(async () => releaseRefresh());
      await waitFor(() => expect(toggle).toBeEnabled());
      expect(client.getQueryData(["widgets"])).toEqual([]);

      await act(() => client.invalidateQueries({ queryKey: ["widgets"], refetchType: "none" }));
      fireEvent.click(nav().getByRole("link", { name: "Übersicht" }));
      expect(screen.queryByText("Wert erste")).toBeNull();
      expect(screen.queryByText("Wert zweite")).toBeNull();
      expect(screen.getByTestId("dashboard-no-widgets")).toBeInTheDocument();
      await waitFor(() => expect(readsAfterDisable).toBe(2));
      expect(layoutWrites).toEqual([]);
    } finally {
      await act(async () => {
        releaseRefresh();
        releaseReturn();
      });
    }
  });

  it("behält Dateien, solange ein anderer Anbieter eingeschaltet ist", async () => {
    enabled.terminal = false;
    otherFilesProvider = true;
    renderApp();
    await screen.findByText("Wert erste");
    for (const area of [nav(), modules()]) {
      expect(area.queryByRole("link", { name: "Terminal" })).toBeNull();
      expect(area.getByRole("link", { name: "Dateien" })).toBeInTheDocument();
    }
  });

  it("aktualisiert bei fremden Änderungen auch die geöffnete Suche", async () => {
    renderApp();
    await screen.findByText("Wert erste");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText("Suchen");
    expect(await screen.findByRole("option", { name: "Terminal" })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "Heimserver terminal" } });
    await screen.findByRole("option", { name: /Heimserver: Terminal/ });
    enabled.terminal = false;
    enabled.fremd = false;
    publish("extension.disabled", "terminal");
    publish("extension.disabled", "fremd");
    await waitFor(() => expect(screen.queryByRole("option", { name: /Heimserver: Terminal/ })).toBeNull());
    fireEvent.change(input, { target: { value: "" } });
    await waitFor(() => {
      expect(screen.queryByRole("option", { name: "Terminal" })).toBeNull();
      expect(screen.queryByRole("option", { name: "Dateien" })).toBeNull();
      for (const page of pages) expect(screen.queryByRole("option", { name: new RegExp(page.title) })).toBeNull();
    });
  });

  it.each(["abgebrochen", "fehlgeschlagen"])("lässt das Modul sichtbar, wenn das Ausschalten %s ist", async (outcome) => {
    renderApp();
    await screen.findByText("Wert erste");
    await settings();
    if (outcome === "abgebrochen") {
      fireEvent.click(screen.getByRole("switch", { name: "Terminal ausschalten" }));
      fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Abbrechen" }));
      expect(changes).toEqual([]);
    } else {
      failDisable = true;
      await disable("Terminal");
      await screen.findByText("Ausschalten fehlgeschlagen");
    }
    expect(nav().getByRole("link", { name: "Terminal" })).toBeInTheDocument();
    fireEvent.click(nav().getByRole("link", { name: "Übersicht" }));
    await waitFor(() => expect(modules().getByRole("link", { name: "Terminal" })).toBeInTheDocument());
  });
});
