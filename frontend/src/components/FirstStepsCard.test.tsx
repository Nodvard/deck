import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildFirstSteps, type ExtensionInfo } from "../lib/firstSteps";
import type { HostOut } from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { FirstStepsCard } from "./FirstStepsCard";

const host = (id: string, extra: Partial<HostOut> = {}): HostOut => ({
  id, name: id, display_name: id, address: "10.0.0.5", os_family: "linux", kind: null, tags: [], managed_tags: [],
  credential: null, is_managed: true, enabled: true, status: "unknown", last_seen_at: null, provider_ext_id: null, ...extra,
});
const ssh = { id: "c1", kind: "ssh_key" as const, username: "lattice", port: 22 };
const ext = (id: string, extra: Partial<ExtensionInfo> = {}): ExtensionInfo => ({
  id, name: id, state: "enabled", has_settings: true, needs_setup: false, ...extra,
});
const WIDGET = {
  id: "summary", ext_id: "backups", title: "Backup-Center", icon: null, description: null, size: { w: 2, h: 2, min_w: 1, min_h: 1 },
  refresh: { interval_s: null, ws_channel: null }, data_endpoint: "widgets/summary",
  view: { kind: "stat", value: "1", label: "Jobs", delta: null, tone: "neutral", sparkline_field: null },
  permissions: [], component: null, default_enabled: true,
};

interface World {
  hosts: HostOut[];
  extensions: ExtensionInfo[];
  widgets: unknown[];
  items: unknown[];
  dismissed: boolean;
  failing: string[];
  demoActive: boolean;
}
let world: World;
let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function installFetch() {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (world.failing.some((f) => url.startsWith(f))) return json({ detail: "kaputt" }, 500);
    if (url === "/demo" && method === "GET") {
      return json({ active: world.demoActive, hosts: world.demoActive ? 5 : 0, notifications: 0, layout_replaced: false, hosts_with_access: [] });
    }
    if (url === "/demo/seed" && method === "POST") {
      world.demoActive = true;
      return json({ active: true, created: true, hosts: 5, notifications: 4, layout_replaced: false, hosts_with_access: [] });
    }
    if (url === "/hosts") return json(world.hosts);
    if (url === "/extensions") return json(world.extensions);
    if (url === "/widgets") return json(world.widgets);
    if (url === "/dashboard/layouts") return json([{ id: "l1", name: "Standard", is_default: true, items: world.items, created_at: "", updated_at: "" }]);
    if (url === "/me/preferences" && method === "GET") return json({ first_steps_dismissed: world.dismissed });
    if (url === "/me/preferences" && method === "PATCH") {
      world.dismissed = JSON.parse(String(init?.body)).first_steps_dismissed;
      return json({ first_steps_dismissed: world.dismissed });
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

/** Wartet, bis alle Abfragen fertig sind und die Karte danach gezeichnet wurde. */
async function settled(client: QueryClient) {
  await waitFor(() => expect(client.isFetching()).toBe(0));
  await act(async () => {});
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, ...render(
    <QueryClientProvider client={client}>
      <MemoryRouter><FirstStepsCard /></MemoryRouter>
    </QueryClientProvider>,
  ) };
}

beforeEach(() => {
  world = { hosts: [], extensions: [ext("terminal", { state: "disabled" }), ext("ntfy", { state: "disabled" })], widgets: [WIDGET], items: [], dismissed: false, failing: [], demoActive: false };
  login(["*"]);
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

function step(id: string) {
  return screen.getByTestId(`first-step-${id}`);
}

describe("buildFirstSteps", () => {
  const base = { can: () => true, hosts: [], extensions: [], placedWidgets: 0, catalogWidgets: 1 };

  it("lässt Schritte ohne Recht oder ohne verlässliche Daten weg", () => {
    expect(buildFirstSteps({ ...base, can: () => false }).map((s) => s.id)).toEqual(["widget"]);
    expect(buildFirstSteps({ ...base, hosts: undefined, extensions: undefined, placedWidgets: undefined }).map((s) => s.id)).toEqual([]);
    expect(buildFirstSteps({ ...base, can: (p) => p === "hosts.write" }).map((s) => s.id)).toEqual(["widget"]); // hosts.read fehlt
  });

  it("Zugang: führt zum ersten Server ohne Zugang, von Hand angelegte zuerst", () => {
    const steps = buildFirstSteps({
      ...base,
      hosts: [host("vm1", { provider_ext_id: "x" }), host("pi"), host("mit", { credential: ssh })],
    });
    expect(steps.find((s) => s.id === "access")?.to).toBe("/settings/hosts/pi");
    expect(steps.find((s) => s.id === "access")?.done).toBe(true);
  });

  it("ein API-Token ist kein SSH-Zugang", () => {
    const token = { id: "t", kind: "api_token" as const, username: "x", port: 8006 };
    const steps = buildFirstSteps({ ...base, hosts: [host("pve", { credential: token, status: "up" })] });
    expect(steps.find((s) => s.id === "access")?.done).toBe(false);
    expect(steps.find((s) => s.id === "check")?.done).toBe(false);
  });

  it("Module: einrichten statt einschalten, sobald etwas läuft; ntfy zählt nur beim Push-Schritt", () => {
    const steps = buildFirstSteps({
      ...base,
      extensions: [ext("proxmox", { name: "Proxmox VE", needs_setup: true }), ext("ntfy", { needs_setup: true })],
    });
    const modules = steps.find((s) => s.id === "modules")!;
    expect(modules.title).toBe("Module einrichten");
    expect(modules.done).toBe(false);
    expect(modules.to).toBe("/settings/extensions/proxmox");
    expect(modules.text).toContain("Proxmox VE braucht noch");
    expect(steps.find((s) => s.id === "push")?.done).toBe(false);
  });
});

describe("FirstStepsCard", () => {
  it("frische Installation: alle Schritte offen, jeder mit Direktlink", async () => {
    world.items = [];
    renderCard();
    expect(await screen.findByRole("heading", { name: "Erste Schritte" })).toBeInTheDocument();
    expect(screen.getByText("0 von 6 erledigt")).toBeInTheDocument();
    expect(within(step("server")).getByRole("link", { name: "Server hinzufügen" })).toHaveAttribute("href", "/settings/hosts");
    expect(within(step("access")).getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts");
    expect(within(step("modules")).getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
    expect(within(step("push")).getByRole("link", { name: "Einschalten" })).toHaveAttribute("href", "/settings/extensions");
    expect(within(step("widget")).getByRole("button", { name: "Zu den Widgets" })).toBeInTheDocument();
    // Sicherung ist nur ein Hinweis, zählt nicht mit und hat kein Häkchen.
    const tip = screen.getByTestId("first-steps-backup");
    expect(within(tip).getByRole("link", { name: /Sicherung/ })).toHaveAttribute("href", "/settings/system");
    expect(screen.queryByTestId("first-step-backup")).toBeNull();
  });

  it("Häkchen entstehen aus den Daten: Server, Zugang, Verbindung, Module, Push, Widget", async () => {
    world.hosts = [host("pi", { credential: ssh, status: "up" })];
    world.extensions = [ext("terminal"), ext("ntfy", { needs_setup: true })];
    world.items = [{ widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} }];
    renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(screen.getByText("5 von 6 erledigt")).toBeInTheDocument();
    for (const id of ["server", "access", "check", "modules", "widget"]) {
      expect(step(id)).toHaveAttribute("data-done", "true");
      expect(within(step(id)).queryByRole("link")).toBeNull(); // erledigt: kein Knopf mehr
    }
    expect(step("push")).toHaveAttribute("data-done", "false");
    expect(within(step("push")).getByRole("link", { name: "Jetzt einrichten" })).toHaveAttribute("href", "/settings/extensions/ntfy");
  });

  it("ein ausgeblendetes Widget zählt nicht als Widget auf dem Dashboard", async () => {
    world.items = [{ widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: { hidden: true } }];
    renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(step("widget")).toHaveAttribute("data-done", "false");
  });

  it("verschwindet von selbst, wenn alles erledigt ist", async () => {
    world.hosts = [host("pi", { credential: ssh, status: "up" })];
    world.extensions = [ext("terminal"), ext("ntfy")];
    world.items = [{ widget_id: "summary", ext_id: "backups", x: 0, y: 0, w: 2, h: 2, config: {} }];
    const { client, container } = renderCard();
    await settled(client);
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByRole("heading", { name: "Erste Schritte" })).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("nur Schritte, für die der Nutzer das Recht hat", async () => {
    login(["hosts.read", "hosts.write"]);
    renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(screen.getByText("0 von 4 erledigt")).toBeInTheDocument();
    expect(screen.queryByTestId("first-step-modules")).toBeNull();
    expect(screen.queryByTestId("first-step-push")).toBeNull();
    expect(screen.queryByTestId("first-steps-backup")).toBeNull(); // settings.write fehlt
    expect(fetchMock.mock.calls.map(([u]) => String(u))).not.toContain("/api/v1/extensions");
  });

  it("ohne Verwaltungsrechte bleibt nur das Widget", async () => {
    login(["notifications.read"]);
    renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(screen.getByText("0 von 1 erledigt")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([u]) => String(u))).not.toContain("/api/v1/hosts");
  });

  it("Ausblenden wird für den Benutzer gespeichert und blendet die Karte aus", async () => {
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Ausblenden" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Erste Schritte" })).toBeNull());
    expect(world.dismissed).toBe(true);
    const patch = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PATCH");
    expect(patch?.[0]).toBe("/api/v1/me/preferences");
    expect(JSON.parse(String((patch?.[1] as RequestInit).body))).toEqual({ first_steps_dismissed: true });
  });

  it("schon ausgeblendet: keine Karte", async () => {
    world.dismissed = true;
    const { client, container } = renderCard();
    await settled(client);
    expect(container.textContent).toBe("");
  });

  it("kann ein Schritt nicht ermittelt werden (Fehler), entfällt er statt zu raten", async () => {
    world.failing = ["/extensions"];
    renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(screen.queryByTestId("first-step-modules")).toBeNull();
    expect(screen.getByTestId("first-step-server")).toBeInTheDocument();
  });

  it("ohne Server und mit Recht: Hinweis mit Knopf „Mit Beispieldaten ansehen“, der sie anlegt", async () => {
    renderCard();
    const tip = await screen.findByTestId("first-steps-demo");
    expect(tip.textContent).toContain("Die Beispieldaten löschst du mit einem Klick wieder");
    fireEvent.click(within(tip).getByRole("button", { name: "Mit Beispieldaten ansehen" }));
    await waitFor(() => expect(world.demoActive).toBe(true));
  });

  it("mit Server oder ohne Recht dafür: kein Beispieldaten-Hinweis", async () => {
    world.hosts = [host("pi")];
    const first = renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(screen.queryByTestId("first-steps-demo")).toBeNull();
    first.unmount();

    world.hosts = [];
    login(["hosts.read", "hosts.write"]); // settings.write fehlt
    renderCard();
    await screen.findByRole("heading", { name: "Erste Schritte" });
    expect(screen.queryByTestId("first-steps-demo")).toBeNull();
    expect(screen.queryByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeNull();
  });

  it("solange Beispieldaten aktiv sind, ist die Karte weg (sie würde „Server angelegt“ vortäuschen)", async () => {
    world.demoActive = true;
    world.hosts = [host("demo-nas", { address: "192.0.2.10" })];
    const { client, container } = renderCard();
    await settled(client);
    expect(container.textContent).toBe("");
  });

  it("Widgets verwalten springt zum Widget-Bereich", async () => {
    const target = document.createElement("div");
    target.id = "dashboard-widgets";
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);
    try {
      renderCard();
      fireEvent.click(await screen.findByRole("button", { name: "Zu den Widgets" }));
      expect(target.scrollIntoView).toHaveBeenCalled();
    } finally {
      target.remove();
    }
  });
});
