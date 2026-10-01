/**
 * `useAppActions`: nach dem Speichern aendert sich die Uebersicht sofort (der Zwischenspeicher von `["overview"]`),
 * und der Aufruf wartet nicht auf das Neuladen -- das dauert bei einem haengenden Server bis zu 12 Sekunden.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { EMPTY_FORM, useAppActions } from "./apps";
import { type AppTileOut, type OverviewOut, useOverview } from "./overview";

const custom = (id: string, name: string, order: number, over: Partial<AppTileOut> = {}): AppTileOut => ({
  id, source: "custom", name, url: "http://192.168.2.1", host: null, host_id: null, state: null, tone: null, image: null,
  icon: null, color: null, group: null, open_in_new_tab: true, sort_order: order, ...over,
});
const detected: AppTileOut = {
  id: "d1", source: "detected", name: "grafana", url: "http://10.0.0.5:3000", host: "docker", host_id: "h1", state: "running", tone: "good",
  image: "x/y", icon: null, color: null, group: null, open_in_new_tab: true, sort_order: null,
};
const overview = (apps: AppTileOut[]): OverviewOut => ({
  services: [], services_running: 0, apps, backups: null, pending_actions: 0, unread_notifications: 0, attention: [], errors: [], generated_at: 1,
});

interface Call { method: string; url: string; body: Record<string, unknown> | null }
let calls: Call[];
let client: QueryClient;
let respond: (call: Call) => Response;

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
  calls = [];
  respond = () => new Response("{}", { status: 200 });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["overview"], overview([custom("a1", "Router", 0), custom("a2", "NAS", 1), custom("a3", "Drucker", 2), detected]));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      method: init?.method ?? "GET",
      url: String(input).replace("/api/v1", ""),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    };
    calls.push(call);
    // Die Uebersicht laedt "ewig": ein Server antwortet nicht.
    if (call.method === "GET" && call.url === "/overview") return new Promise<Response>(() => {});
    return respond(call);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function setup() {
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  // Wie im Cockpit: jemand beobachtet die Uebersicht (sonst gaebe es nichts neu zu laden).
  renderHook(() => useOverview(), { wrapper });
  return renderHook(() => useAppActions(), { wrapper }).result;
}

const apps = () => (client.getQueryData(["overview"]) as OverviewOut).apps;
const order = () => apps().map((a) => a.id);
const withTimeout = <T,>(work: Promise<T>) =>
  Promise.race([work, new Promise<never>((_, reject) => setTimeout(() => reject(new Error("wartet auf das Neuladen der Übersicht")), 1000))]);

const form = { ...EMPTY_FORM, name: "Pi-hole", url: "192.168.2.72/admin", group: "Netzwerk", icon: "shield-check", color: "#10b981" };
const saved = (over: Record<string, unknown> = {}) => ({
  id: "neu", name: "Pi-hole", url: "http://192.168.2.72/admin", icon: "shield-check", color: "#10b981", group: "Netzwerk", sort_order: 3,
  open_in_new_tab: true, host_id: null, host: null, created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-01T10:00:00Z", ...over,
});

describe("useAppActions: sofort sichtbar, ohne auf das Neuladen zu warten", () => {
  it("anlegen: die neue Kachel steht sofort bei den eigenen Apps, vor den erkannten Diensten", async () => {
    respond = () => new Response(JSON.stringify(saved()), { status: 201 });
    const actions = setup();
    await act(async () => { await withTimeout(actions.current.create(form)); });
    expect(order()).toEqual(["a1", "a2", "a3", "neu", "d1"]);
    expect(apps()[3]).toMatchObject({ source: "custom", name: "Pi-hole", url: "http://192.168.2.72/admin", icon: "shield-check", group: "Netzwerk", sort_order: 3 });
    expect(calls.some((c) => c.method === "POST" && c.url === "/apps")).toBe(true);
  });

  it("anlegen mit knapper Antwort (nur Kennung und Name) macht die Kachel aus den Eingaben", async () => {
    respond = () => new Response(JSON.stringify({ id: "neu", name: "Pi-hole" }), { status: 201 });
    const actions = setup();
    await act(async () => { await withTimeout(actions.current.create(form)); });
    expect(apps()[3]).toMatchObject({ id: "neu", name: "Pi-hole", url: "http://192.168.2.72/admin", group: "Netzwerk", sort_order: 3 });
  });

  it("ändern: die Kachel wird ersetzt und bleibt an ihrer Stelle", async () => {
    respond = () => new Response(JSON.stringify(saved({ id: "a2", name: "Mein NAS", sort_order: 1, group: null })), { status: 200 });
    const actions = setup();
    await act(async () => { await withTimeout(actions.current.update("a2", { ...form, name: "Mein NAS", group: "" })); });
    expect(order()).toEqual(["a1", "a2", "a3", "d1"]);
    expect(apps()[1]).toMatchObject({ name: "Mein NAS", group: null, sort_order: 1 });
    expect(calls.find((c) => c.method === "PATCH")?.url).toBe("/apps/a2");
  });

  it("löschen: die Kachel ist sofort weg (ein zweiter Klick auf Löschen trifft sie nicht mehr)", async () => {
    respond = () => new Response(null, { status: 204 });
    const actions = setup();
    await act(async () => { await withTimeout(actions.current.remove("a2")); });
    expect(order()).toEqual(["a1", "a3", "d1"]);
  });

  it("Reihenfolge: die eigenen Apps stehen sofort in der neuen Reihenfolge, Positionen neu durchgezählt", async () => {
    respond = () => new Response("[]", { status: 200 });
    const actions = setup();
    await act(async () => { await withTimeout(actions.current.reorder(["a3", "a1"])); });
    expect(order()).toEqual(["a3", "a1", "a2", "d1"]);
    expect(apps().filter((a) => a.source === "custom").map((a) => a.sort_order)).toEqual([0, 1, 2]);
  });

  it("lädt die Übersicht danach trotzdem neu (der Stand des Servers gilt)", async () => {
    respond = () => new Response(null, { status: 204 });
    const actions = setup();
    const before = calls.filter((c) => c.url === "/overview").length;
    await act(async () => { await withTimeout(actions.current.remove("a2")); });
    await vi.waitFor(() => expect(calls.filter((c) => c.url === "/overview").length).toBeGreaterThan(before));
  });

  it("bei einem Fehler bleibt die Übersicht unverändert und der Fehler geht an den Aufrufer", async () => {
    respond = () => new Response(JSON.stringify({ detail: "Diese App gibt es nicht (mehr)." }), { status: 404 });
    const actions = setup();
    await act(async () => {
      await expect(withTimeout(actions.current.remove("a2"))).rejects.toThrow("Diese App gibt es nicht (mehr).");
    });
    expect(order()).toEqual(["a1", "a2", "a3", "d1"]);
  });
});
