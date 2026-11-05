import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ActionState } from "../../../_shared/frontend/src/actions";

import { ContainersPage } from "./ContainersPage";
import { hostAction } from "./hostAction";
import { validImage, withoutTag } from "./ImagePull";
import { ResourceList } from "./Resources";

const OWN = "sha256:" + "b".repeat(64);
const TWO_NAMES = "sha256:" + "c".repeat(64);
const BUSYBOX = "sha256:" + "d".repeat(64);
const UNUSED_NET = "2".repeat(64);

const ROLLBACK = "sha256:" + "e".repeat(64);
const IMAGES = {
  host_id: "h1",
  images: [
    { id: ROLLBACK, short_id: "eeeeeeeeeeee", names: ["nodvard-deck:previous"], size_bytes: 410_000_000, created_at: null,
      used_by: [], in_use: false, dangling: false, removable: false, rollback: true },
    { id: BUSYBOX, short_id: "dddddddddddd", names: ["busybox:1.37"], size_bytes: 6_770_000, created_at: "2026-09-01 10:00:00",
      used_by: ["web"], in_use: true, dangling: false, removable: false },
    { id: OWN, short_id: "bbbbbbbbbbbb", names: ["eigenes-image:1"], size_bytes: 6_790_000, created_at: null,
      used_by: [], in_use: false, dangling: false, removable: true },
    { id: TWO_NAMES, short_id: "cccccccccccc", names: ["app:1", "app:latest"], size_bytes: null, created_at: null,
      used_by: [], in_use: false, dangling: false, removable: false },
  ],
};
const VOLUMES = {
  host_id: "h1",
  volumes: [
    { name: "daten", driver: "local", size_bytes: 0, anonymous: false, compose_project: null, used_by: ["web"], in_use: true, removable: false },
    { name: "leer", driver: "local", size_bytes: 1024, anonymous: false, compose_project: "demo", used_by: [], in_use: false, removable: true },
  ],
};
const NETWORKS = {
  host_id: "h1",
  error: null,
  networks: [
    { id: "1".repeat(64), short_id: "111111111111", name: "bridge", driver: "bridge", internal: false, builtin: true,
      compose_project: null, used_by: ["alt"], in_use: true, removable: false },
    { id: UNUSED_NET, short_id: "222222222222", name: "ungenutzt", driver: "bridge", internal: true, builtin: false,
      compose_project: null, used_by: [], in_use: false, removable: true },
  ],
};

interface Call { url: string; method: string; body?: unknown }

const HOSTS = { hosts: [{ id: "h1", name: "pi" }, { id: "h2", name: "nas" }, { id: "h3", name: "leerer-server" }] };

function resourceFetch(calls: Call[], overrides: Record<string, (url: string) => Response | Promise<Response>> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    for (const [part, answer] of Object.entries(overrides)) if (url.includes(part)) return answer(url);
    if (url.endsWith("/ext/docker/hosts")) return Response.json(HOSTS);
    if (url.endsWith("/images")) return Response.json(IMAGES);
    if (url.endsWith("/volumes")) return Response.json(VOLUMES);
    if (url.endsWith("/networks")) return Response.json(NETWORKS);
    if (url.includes("/actions/docker.") && method === "POST") return Response.json({ id: "a1", status: "proposed", risk: "high" }, { status: 202 });
    if (url.endsWith("/actions/a1/approve")) return Response.json({ id: "a1", status: "succeeded", result: { output: "Volume leer gelöscht." } });
    if (url.includes("/ext/docker/widgets/matrix")) return Response.json({ data: SERVICES, meta: {} });
    if (url.includes("/ext/docker/image-updates")) return Response.json({ hosts: {}, data: {} });
    if (url.includes("/ext/docker/stats")) return Response.json({ data: {} });
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

const SERVICES = [
  { id: "h1:web", name: "web", host: "pi", host_id: "h1", container: "web", is_self: false, state: "running", status: "Up", tone: "good", url: null },
  { id: "h2:db", name: "db", host: "nas", host_id: "h2", container: "db", is_self: false, state: "running", status: "Up", tone: "good", url: null },
];

function setDeck(permissions: (p: string) => boolean = () => true, confirm = true) {
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(confirm),
    promptDialog: vi.fn(),
    hasPermission: vi.fn(permissions),
  };
}

beforeEach(() => {
  window.history.replaceState({}, "", "/ext/docker/matrix");
  setDeck();
});

const LINKED = {
  ...NETWORKS,
  containers: [
    { name: "alt", compose_project: null, self: false }, { name: "web", compose_project: "shop", self: false },
    { name: "nodvard-dock", compose_project: "nodvard-dock", self: true },
  ],
  networks: [
    ...NETWORKS.networks,
    { id: "3".repeat(64), short_id: "333333333333", name: "host", driver: "host", internal: false, builtin: true,
      compose_project: null, used_by: [], in_use: true, removable: false },
    { id: "4".repeat(64), short_id: "444444444444", name: "ingress", driver: "overlay", internal: false, builtin: false,
      compose_project: null, used_by: ["web"], in_use: true, removable: false },
    { id: "5".repeat(64), short_id: "555555555555", name: "dock", driver: "bridge", internal: false, builtin: false,
      compose_project: "nodvard-dock", used_by: ["nodvard-dock", "web"], in_use: true, removable: false },
  ],
};

describe("Netze anlegen und verbinden", () => {
  it("zeigt den Fehler einer automatisch ausgeführten Netzanlage im Formular", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, {
      "/actions/docker.network_create": () => Response.json({
        id: "a1", status: "failed", result: { error: "Ein Netz mit diesem Namen gibt es schon." },
      }),
    }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Netz anlegen" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name des Netzes" }), { target: { value: "intern" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Fehlgeschlagen: Ein Netz mit diesem Namen gibt es schon.");
    expect(screen.getByRole("textbox", { name: "Name des Netzes" })).toHaveValue("intern");
    expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
    expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/networks"))).toHaveLength(1);
  });

  it.each(["approved", "executing"])("behält die Kennung bei unterbrochener Ergebnisabfrage im Status %s", async (status) => {
    vi.stubGlobal("fetch", resourceFetch([], {
      "/actions/docker.network_connect": () => Response.json({ id: "a1", status }),
    }));
    const controller = new AbortController();
    controller.abort();
    const result = await hostAction("h1", "docker.network_connect", "Container verbinden.", controller.signal);
    expect(result).toMatchObject({ waiting: true, failed: false, actionId: "a1" });
  });

  describe.each(["Verbinden", "Trennen"] as const)("%s bei zugeklapptem Containerbereich", (label) => {
    it.each<[string, ActionState, string]>([
      ["Erfolg", { status: "succeeded", result: { output: "Verbindung geändert." } }, "Verbindung geändert."],
      ["Fehler", { status: "failed", result: { error: "Container nicht erreichbar." } }, "Fehlgeschlagen: Container nicht erreichbar."],
    ])("zeigt nach laufender Ausführung den %s und beendet die Abfrage", async (_, outcome, expected) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        let settled = false;
        const calls: Call[] = [];
        vi.stubGlobal("fetch", resourceFetch(calls, {
          "/networks": () => Response.json(LINKED),
          "/actions/a1/approve": () => Response.json({ id: "a1", status: "executing" }, { status: 202 }),
          "/actions/a1": () => Response.json(settled ? outcome : { id: "a1", status: "executing" }),
        }));
        const { unmount } = render(<ResourceList hostId="h1" kind="networks" />);
        const connect = label === "Verbinden";
        const toggle = `Container: ${connect ? "ungenutzt" : "bridge"}`;
        fireEvent.click(await screen.findByRole("button", { name: toggle }));
        if (connect) fireEvent.change(screen.getByRole("combobox"), { target: { value: "web" } });
        fireEvent.click(screen.getByRole("button", { name: connect ? "Verbinden" : "Trennen: alt" }));
        await waitFor(() => expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(true));
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(calls.filter((c) => c.url.endsWith("/actions/a1"))).toHaveLength(1);
        fireEvent.click(screen.getByRole("button", { name: toggle }));
        expect(screen.queryByTestId(`network-links-${connect ? "ungenutzt" : "bridge"}`)).toBeNull();
        settled = true;
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(screen.getByRole("status")).toHaveTextContent(`${label}: ${expected}`);
        expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/networks"))).toHaveLength(2);
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(calls.filter((c) => c.url.endsWith("/actions/a1"))).toHaveLength(2);
        unmount();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it("legt ein Netz über die Freigabe an, nur mit gültigem Namen", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Netz anlegen" }));
    const name = screen.getByRole("textbox", { name: "Name des Netzes" });
    fireEvent.change(name, { target: { value: "host" } });
    expect(screen.getByText(/nicht bridge, host, none oder default/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anlegen" })).toBeDisabled();
    fireEvent.change(name, { target: { value: "default" } });
    expect(screen.getByRole("button", { name: "Anlegen" })).toBeDisabled();
    fireEvent.change(name, { target: { value: "intern" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Netz anlegen:");
    const post = calls.find((c) => c.url === "/api/v1/hosts/h1/actions/docker.network_create");
    expect(post?.body).toEqual(expect.objectContaining({ payload: { name: "intern", internal: true } }));
    expect(screen.queryByTestId("network-create")).toBeNull();
  });

  it("lädt die Netze neu, sobald ein vorgeschlagenes Netz freigegeben und angelegt ist", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      setDeck((p) => !p.startsWith("actions.approve"));
      let status = "proposed";
      const calls: Call[] = [];
      vi.stubGlobal("fetch", resourceFetch(calls, { "/actions/a1": () => Response.json({ id: "a1", status }) }));
      render(<ResourceList hostId="h1" kind="networks" />);
      fireEvent.click(await screen.findByRole("button", { name: "Netz anlegen" }));
      fireEvent.change(screen.getByRole("textbox", { name: "Name des Netzes" }), { target: { value: "intern" } });
      fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
      const lists = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/networks")).length;
      await waitFor(() => expect(lists()).toBe(2));
      status = "succeeded";
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(lists()).toBe(3));
      await vi.advanceTimersByTimeAsync(3100);
      expect(calls.filter((c) => c.url.endsWith("/actions/a1")).length).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each<[string, ActionState | null, string]>([
    ["Erfolg", { status: "succeeded", result: { output: "Netz intern angelegt." } }, "Netz intern angelegt."],
    ["Fehler", { status: "failed", result: { error: "Das Netz gibt es schon." } }, "Fehlgeschlagen: Das Netz gibt es schon."],
    ["Ablehnung", { status: "denied", gate_decision: { rule: "user:reject", user_reason: "Nicht benötigt." } }, "Abgelehnt: Nicht benötigt."],
    ["Ablauf", { status: "expired" }, "Abgelaufen"],
    ["Verwerfen", { status: "dismissed" }, "Verworfen"],
    ["fehlender Aktion", null, "Aktion nicht mehr verfügbar."],
  ])("zeigt nach %s das endgültige Ergebnis des vorgeschlagenen Netzes", async (_, outcome, expected) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      setDeck((p) => !p.startsWith("actions.approve"));
      let settled = false;
      const calls: Call[] = [];
      const created = { ...NETWORKS.networks[1], id: "6".repeat(64), name: "intern" };
      vi.stubGlobal("fetch", resourceFetch(calls, {
        "/actions/a1": () => settled
          ? Response.json(outcome ?? {}, { status: outcome ? 200 : 404 })
          : Response.json({ id: "a1", status: "executing" }),
        "/networks": () => Response.json({ ...NETWORKS, networks: settled && outcome?.status === "succeeded"
          ? [...NETWORKS.networks, created] : NETWORKS.networks }),
      }));
      render(<ResourceList hostId="h1" kind="networks" />);
      fireEvent.click(await screen.findByRole("button", { name: "Netz anlegen" }));
      fireEvent.change(screen.getByRole("textbox", { name: "Name des Netzes" }), { target: { value: "intern" } });
      fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      expect(screen.getByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
      settled = true;
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(`Netz anlegen: ${expected}`));
      expect(screen.getByRole("status")).not.toHaveTextContent("Freigabe durch einen Admin nötig");
      expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/networks"))).toHaveLength(3);
      if (outcome?.status === "succeeded") expect(screen.getByTestId(`resource-${created.id}`)).toHaveTextContent("intern");
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      expect(calls.filter((c) => c.url.endsWith("/actions/a1"))).toHaveLength(2);
      expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("verbindet und trennt nach Rückfrage, mit Hinweis bei Stacks", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, { "/networks": () => Response.json(LINKED) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Container: ungenutzt" }));
    const panel = screen.getByTestId("network-links-ungenutzt");
    expect(panel).toHaveTextContent("An diesem Netz hängt noch kein Container.");
    expect(within(panel).getByRole("button", { name: "Verbinden" })).toBeDisabled();
    fireEvent.change(within(panel).getByRole("combobox"), { target: { value: "web" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/docker.network_connect"))).toBe(true));
    const question = vi.mocked(window.__nodvardDeck!.confirmDialog).mock.calls[0][0];
    expect(question).toContain("„web“ mit dem Netz „ungenutzt“ verbinden?");
    expect(question).toContain("Er gehört zum Stack „shop“: Erstellt Compose ihn neu");
    expect(calls.find((c) => c.url.endsWith("/actions/docker.network_connect"))?.body)
      .toEqual(expect.objectContaining({ payload: { network: UNUSED_NET, container: "web" } }));

    await screen.findByRole("status");
    fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
    fireEvent.click(within(screen.getByTestId("network-links-bridge")).getByRole("button", { name: "Trennen: alt" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/actions/docker.network_disconnect"))).toBe(true));
    const second = vi.mocked(window.__nodvardDeck!.confirmDialog).mock.calls[1][0];
    expect(second).toBe("„alt“ vom Netz „bridge“ trennen? Dienste, die ihn über dieses Netz erreichen, finden ihn danach nicht mehr. "
      + "Es ist sein einziges Netz: Danach hat er kein Netz mehr, keine veröffentlichten Ports und keinen Zugang ins Internet.");
    expect(calls.find((c) => c.url.endsWith("/actions/docker.network_disconnect"))?.body)
      .toEqual(expect.objectContaining({ payload: { network: "1".repeat(64), container: "alt" } }));
  });

  it("schickt nach „Abbrechen“ nichts und bietet host und none nicht an", async () => {
    setDeck(() => true, false);
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, { "/networks": () => Response.json(LINKED) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    await screen.findByTestId(`resource-${"3".repeat(64)}`);
    expect(screen.queryByRole("button", { name: "Container: host" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Container: ingress" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Container: bridge" }));
    fireEvent.click(screen.getByRole("button", { name: "Trennen: alt" }));
    await waitFor(() => expect(window.__nodvardDeck?.confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("lässt den eigenen Container in Ruhe und warnt nur beim letzten Netz", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, { "/networks": () => Response.json(LINKED) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Container: dock" }));
    const panel = screen.getByTestId("network-links-dock");
    expect(panel).toHaveTextContent("nodvard-dock (Stack nodvard-dock) – Nodvard Deck selbst, bleibt verbunden");
    expect(within(panel).queryByRole("button", { name: "Trennen: nodvard-dock" })).toBeNull();
    expect([...within(panel).getByRole("combobox").querySelectorAll("option")].map((o) => o.value)).toEqual(["", "alt"]);
    fireEvent.click(within(panel).getByRole("button", { name: "Trennen: web" }));
    await waitFor(() => expect(window.__nodvardDeck?.confirmDialog).toHaveBeenCalled());
    expect(vi.mocked(window.__nodvardDeck!.confirmDialog).mock.calls[0][0]).not.toContain("einziges Netz");
    expect(vi.mocked(window.__nodvardDeck!.confirmDialog).mock.calls[0][0]).toContain("hängt er wieder an den Netzen aus der Compose-Datei");
  });

  it("sagt ohne Container, dass es keine gibt, statt alle für verbunden zu halten", async () => {
    vi.stubGlobal("fetch", resourceFetch([], { "/networks": () => Response.json({ ...NETWORKS, containers: [] }) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Container: ungenutzt" }));
    const panel = screen.getByTestId("network-links-ungenutzt");
    expect(panel).toHaveTextContent("Auf diesem Server gibt es keine Container.");
    expect(panel).not.toHaveTextContent("hängen schon an diesem Netz");
  });

  it("lässt das Formular bei einer Ablehnung mit Grund offen und lädt nichts neu", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, {
      "/approve": () => Response.json({ id: "a1", status: "failed", result: { error: "Das Netz „intern“ gibt es schon." } }),
    }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Netz anlegen" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name des Netzes" }), { target: { value: "intern" } });
    const lists = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/networks")).length;
    const before = lists();
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das Netz „intern“ gibt es schon.");
    expect(screen.getByRole("textbox", { name: "Name des Netzes" })).toHaveValue("intern");
    expect(screen.queryByRole("status")).toBeNull();
    expect(lists()).toBe(before);
  });

  it("hält ein abgelehntes Trennen nicht für erledigt", async () => {
    vi.stubGlobal("fetch", resourceFetch([], {
      "/networks": () => Response.json(LINKED),
      "/approve": () => Response.json({ id: "a1", status: "denied", result: null }),
    }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
    fireEvent.click(screen.getByRole("button", { name: "Trennen: alt" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Trennen:");
    expect(screen.getByTestId("network-links-bridge")).toBeInTheDocument();
  });

  it("zeigt das Trennen wieder an, wenn der Vorschlag abgelehnt wurde", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let status = "proposed";
      vi.stubGlobal("fetch", resourceFetch([], {
        "/networks": () => Response.json(LINKED),
        "/actions/a1": () => Response.json({ id: "a1", status }),
      }));
      setDeck((p) => !p.startsWith("actions.approve"));
      render(<ResourceList hostId="h1" kind="networks" />);
      fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
      fireEvent.click(screen.getByRole("button", { name: "Trennen: alt" }));
      await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Trennen: vorgeschlagen"));
      fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
      expect(within(screen.getByTestId("network-links-bridge")).getByText("wartet auf Freigabe")).toBeInTheDocument();
      status = "denied";
      await vi.advanceTimersByTimeAsync(3100);
      expect(await screen.findByRole("button", { name: "Trennen: alt" })).toBeInTheDocument();
      expect(within(screen.getByTestId("network-links-bridge")).queryByText("wartet auf Freigabe")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  describe.each(["Verbinden", "Trennen"] as const)("%s nach fremder Freigabe", (label) => {
    it.each<[string, ActionState | null, string]>([
      ["Erfolg mit Ausgabe", { status: "succeeded", result: { output: "Verbindung geändert." } }, "Verbindung geändert."],
      ["Erfolg ohne Ausgabe", { status: "succeeded" }, "Ausgeführt"],
      ["Fehler", { status: "failed", result: { error: "Container nicht erreichbar." } }, "Fehlgeschlagen: Container nicht erreichbar."],
      ["Ablehnung", { status: "denied", gate_decision: { rule: "user:reject", user_reason: "Nicht benötigt." } }, "Abgelehnt: Nicht benötigt."],
      ["Ablauf", { status: "expired" }, "Abgelaufen"],
      ["Verwerfen", { status: "dismissed" }, "Verworfen"],
      ["fehlender Aktion", null, "Aktion nicht mehr verfügbar."],
    ])("zeigt nach %s das endgültige Ergebnis", async (_, outcome, expected) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        setDeck((p) => !p.startsWith("actions.approve"));
        let settled = false;
        const calls: Call[] = [];
        vi.stubGlobal("fetch", resourceFetch(calls, {
          "/networks": () => Response.json(LINKED),
          "/actions/a1": () => settled
            ? Response.json(outcome ?? {}, { status: outcome ? 200 : 404 })
            : Response.json({ id: "a1", status: "executing" }),
        }));
        render(<ResourceList hostId="h1" kind="networks" />);
        const connect = label === "Verbinden";
        fireEvent.click(await screen.findByRole("button", { name: `Container: ${connect ? "ungenutzt" : "bridge"}` }));
        if (connect) fireEvent.change(screen.getByRole("combobox"), { target: { value: "web" } });
        fireEvent.click(screen.getByRole("button", { name: connect ? "Verbinden" : "Trennen: alt" }));
        await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(`${label}: vorgeschlagen`));
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(screen.getByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
        settled = true;
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(`${label}: ${expected}`));
        expect(screen.getByRole("status")).not.toHaveTextContent("Freigabe durch einen Admin nötig");
        expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/networks"))).toHaveLength(3);
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(calls.filter((c) => c.url.endsWith("/actions/a1"))).toHaveLength(2);
        expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it("lässt die Liste bei einem Fehler offen und zeigt danach „wartet auf Freigabe“", async () => {
    let broken = true;
    vi.stubGlobal("fetch", resourceFetch([], {
      "/networks": () => Response.json(LINKED),
      "/actions/docker.network_disconnect": () => (broken ? Response.json({ detail: "Gate nicht erreichbar" }, { status: 503 })
        : Response.json({ id: "a1", status: "proposed", risk: "medium" }, { status: 202 })),
    }));
    setDeck((p) => !p.startsWith("actions.approve"));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
    fireEvent.click(screen.getByRole("button", { name: "Trennen: alt" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Fehler: Gate nicht erreichbar");
    expect(await screen.findByTestId("network-links-bridge")).toBeInTheDocument();
    broken = false;
    fireEvent.click(await screen.findByRole("button", { name: "Trennen: alt" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Trennen: vorgeschlagen"));
    await waitFor(() => expect(screen.queryByTestId("network-links-bridge")).toBeNull());
    fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
    expect(within(screen.getByTestId("network-links-bridge")).getByText("wartet auf Freigabe")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Trennen: alt" })).toBeNull();
  });

  it("zeigt ohne Server-Rechte weder Anlegen noch Verbinden", async () => {
    setDeck((p) => p !== "hosts.execute");
    vi.stubGlobal("fetch", resourceFetch([], { "/networks": () => Response.json(LINKED) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    await screen.findByTestId("resources-networks");
    expect(screen.queryByRole("button", { name: "Netz anlegen" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Container:/ })).toBeNull();
  });
});

describe("Image holen", () => {
  it("holt ein Image über die Freigabe, nur mit gültigem Namen, und lädt die Liste neu", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, {
      "/actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { output: "Image „nginx:1.27“ geholt." } }),
    }));
    render(<ResourceList hostId="h1" kind="images" />);
    fireEvent.click(await screen.findByRole("button", { name: "Image holen" }));
    const form = within(screen.getByTestId("image-pull"));
    const input = form.getByRole("textbox", { name: "Name des Images" });
    fireEvent.change(input, { target: { value: "-a" } });
    expect(form.getByText(/So heißt kein Image/)).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Holen" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "nginx" } });
    expect(form.getByText(/Ohne Tag holt Docker „latest“/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "nginx:1.27" } });
    expect(form.queryByText(/Ohne Tag/)).toBeNull();
    fireEvent.click(form.getByRole("button", { name: "Holen" }));
    await screen.findByText("Image holen: Image „nginx:1.27“ geholt.");
    const post = calls.find((c) => c.method === "POST" && c.url.includes("/actions/docker.image_pull"));
    expect(post?.url).toBe("/api/v1/hosts/h1/actions/docker.image_pull");
    expect(post?.body).toEqual({ payload: { image: "nginx:1.27" }, reason: "Image über die Docker-Seite holen." });
    expect(screen.queryByTestId("image-pull")).toBeNull();
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/images")).length).toBe(2));
  });

  it.each<[string, ActionState | null, string]>([
    ["Erfolg mit Ausgabe", { status: "succeeded", result: { output: "Image geholt." } }, "Image geholt."],
    ["Erfolg ohne Ausgabe", { status: "succeeded" }, "Ausgeführt"],
    ["Fehler", { status: "failed", result: { error: "Registry nicht erreichbar." } }, "Fehlgeschlagen: Registry nicht erreichbar."],
    ["Ablehnung", { status: "denied", gate_decision: { rule: "user:reject", user_reason: "Nicht benötigt." } }, "Abgelehnt: Nicht benötigt."],
    ["Ablauf", { status: "expired" }, "Abgelaufen"],
    ["Verwerfen", { status: "dismissed" }, "Verworfen"],
    ["fehlender Aktion", null, "Aktion nicht mehr verfügbar."],
  ])("lädt die Images nach %s neu und zeigt das Ergebnis des vorgeschlagenen Holens", async (_, outcome, expected) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      setDeck((p) => !p.startsWith("actions.approve"));
      let settled = false;
      const calls: Call[] = [];
      vi.stubGlobal("fetch", resourceFetch(calls, {
        "/actions/a1": () => settled
          ? Response.json(outcome ?? {}, { status: outcome ? 200 : 404 })
          : Response.json({ id: "a1", status: "proposed" }),
      }));
      render(<ResourceList hostId="h1" kind="images" />);
      fireEvent.click(await screen.findByRole("button", { name: "Image holen" }));
      fireEvent.change(screen.getByRole("textbox", { name: "Name des Images" }), { target: { value: "nginx:1.27" } });
      fireEvent.click(screen.getByRole("button", { name: "Holen" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
      const lists = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/images")).length;
      await waitFor(() => expect(lists()).toBe(2));
      settled = true;
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      await waitFor(() => expect(lists()).toBe(3));
      expect(screen.getByRole("status")).toHaveTextContent(`Image holen: ${expected}`);
      expect(screen.getByRole("status")).not.toHaveTextContent("Freigabe durch einen Admin nötig");
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      expect(calls.filter((c) => c.url.endsWith("/actions/a1"))).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ["scheitert die Aktion", {
      "/actions/a1/approve": () => Response.json({ id: "a1", status: "failed", result: { error: "Diesen Tag gibt es bei der Registry nicht." } }),
    }],
    ["scheitert die Anfrage", { "/actions/docker.image_pull": () => Response.json({ detail: "Server nicht gefunden." }, { status: 404 }) }],
  ])("lässt das Formular mit dem Namen offen, %s", async (_case, overrides) => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, overrides));
    render(<ResourceList hostId="h1" kind="images" />);
    fireEvent.click(await screen.findByRole("button", { name: "Image holen" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Name des Images" }), { target: { value: "nginx:9.9" } });
    fireEvent.click(screen.getByRole("button", { name: "Holen" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Diesen Tag gibt es|Server nicht gefunden/);
    expect(screen.getByRole("textbox", { name: "Name des Images" })).toHaveValue("nginx:9.9");
    expect(calls.filter((c) => c.url.endsWith("/images")).length).toBe(1);
  });

  it("zeigt ohne Server-Rechte keinen Knopf", async () => {
    setDeck((p) => p !== "hosts.execute");
    vi.stubGlobal("fetch", resourceFetch([]));
    render(<ResourceList hostId="h1" kind="images" />);
    await screen.findByTestId(`resource-${OWN}`);
    expect(screen.queryByRole("button", { name: "Image holen" })).toBeNull();
  });

  it("erkennt Namen und fehlende Tags wie Docker", () => {
    for (const ok of ["nginx", "nginx:1.27", "ghcr.io/besitzer/app:1.0", "registry.local:5000/team/app", `app@sha256:${"a".repeat(64)}`]) {
      expect(validImage(ok)).toBe(true);
    }
    for (const bad of ["", "-a", "Nginx", "nginx latest", "nginx;id", "nginx:-1", `${"a/".repeat(128)}b`]) expect(validImage(bad)).toBe(false);
    expect(withoutTag("registry.local:5000/team/app")).toBe(true);
    expect(withoutTag("ghcr.io/besitzer/app:1.0")).toBe(false);
    expect(withoutTag(`app@sha256:${"a".repeat(64)}`)).toBe(false);
  });
});

describe("Abschlüsse ausstehender Ressourcenaktionen", () => {
  describe.each([
    ["images", "Image löschen: eigenes-image:1", "Image löschen", OWN],
    ["volumes", "Volume löschen: leer", "Volume löschen", "leer"],
    ["networks", "Netz löschen: ungenutzt", "Netz löschen", UNUSED_NET],
  ] as const)("Löschen in %s", (kind, button, label, key) => {
    it.each<[string, ActionState | null, string]>([
      ["Erfolg mit Ausgabe", { status: "succeeded", result: { output: "Ressource gelöscht." } }, "Ressource gelöscht."],
      ["Erfolg ohne Ausgabe", { status: "succeeded" }, "Ausgeführt"],
      ["Fehler", { status: "failed", result: { error: "Ressource wird noch benutzt." } }, "Fehlgeschlagen: Ressource wird noch benutzt."],
      ["Ablehnung", { status: "denied", gate_decision: { rule: "user:reject", user_reason: "Wird noch benötigt." } }, "Abgelehnt: Wird noch benötigt."],
      ["Ablauf", { status: "expired" }, "Abgelaufen"],
      ["Verwerfen", { status: "dismissed" }, "Verworfen"],
      ["fehlender Aktion", null, "Aktion nicht mehr verfügbar."],
    ])("zeigt nach %s das endgültige Ergebnis", async (_, outcome, expected) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        setDeck((p) => !p.startsWith("actions.approve"));
        let settled = false;
        const calls: Call[] = [];
        vi.stubGlobal("fetch", resourceFetch(calls, {
          "/actions/a1": () => settled
            ? Response.json(outcome ?? {}, { status: outcome ? 200 : 404 })
            : Response.json({ id: "a1", status: "executing" }),
        }));
        render(<ResourceList hostId="h1" kind={kind} />);
        fireEvent.click(await screen.findByRole("button", { name: button }));
        await waitFor(() => expect(within(screen.getByTestId(`resource-${key}`)).getByText("wartet auf Freigabe")).toBeInTheDocument());
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(screen.getByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
        settled = true;
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(`${label}: ${expected}`));
        expect(screen.getByRole("status")).not.toHaveTextContent("Freigabe durch einen Admin nötig");
        expect(screen.getByRole("button", { name: button })).toBeEnabled();
        expect(calls.filter((c) => c.method === "GET" && c.url.endsWith(`/${kind}`))).toHaveLength(3);
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(calls.filter((c) => c.url.endsWith("/actions/a1"))).toHaveLength(2);
        expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it.each(["Anlegen", "Verbinden und Trennen", "alle vier Aktionsarten"])(
    "behält alle Ergebnisse bei gleichzeitigem Abschluss: %s", async (scenario) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        setDeck((p) => !p.startsWith("actions.approve"));
        let settled = false;
        const calls: Call[] = [];
        const outcomes = new Map<string, ActionState>();
        const proposed = (id: string, outcome: ActionState) => {
          outcomes.set(id, outcome);
          return Response.json({ id, status: "proposed", risk: "high" }, { status: 202 });
        };
        let creates = 0;
        vi.stubGlobal("fetch", resourceFetch(calls, {
          "/networks": () => Response.json(LINKED),
          "/actions/docker.network_create": () => proposed(`create${++creates}`, creates === 1
            ? { status: "succeeded", result: { output: "Netz intern angelegt." } }
            : { status: "failed", result: { error: "Netz extern existiert bereits." } }),
          "/actions/docker.network_remove": () => proposed("remove", { status: "succeeded", result: { output: "Netz ungenutzt gelöscht." } }),
          "/actions/docker.network_connect": () => proposed("connect", { status: "succeeded", result: { output: "web verbunden." } }),
          "/actions/docker.network_disconnect": () => proposed("disconnect", {
            status: "denied", gate_decision: { rule: "user:reject", user_reason: "alt bleibt verbunden." },
          }),
          "/actions/": (url) => {
            const id = url.split("/actions/")[1];
            if (!outcomes.has(id)) throw new Error(`Unbekannte Aktion: ${id}`);
            return Response.json(settled ? outcomes.get(id) : { id, status: "executing" });
          },
        }));
        render(<ResourceList hostId="h1" kind="networks" />);
        const expected: string[] = [];
        const create = async (name: string) => {
          fireEvent.click(await screen.findByRole("button", { name: "Netz anlegen" }));
          fireEvent.change(screen.getByRole("textbox", { name: "Name des Netzes" }), { target: { value: name } });
          fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
          await waitFor(() => expect(screen.queryByTestId("network-create")).toBeNull());
        };
        if (scenario !== "Verbinden und Trennen") {
          await create("intern");
          expected.push("Netz anlegen: Netz intern angelegt.");
          if (scenario === "Anlegen") {
            await create("extern");
            expected.push("Netz anlegen: Fehlgeschlagen: Netz extern existiert bereits.");
          } else {
            fireEvent.click(await screen.findByRole("button", { name: "Netz löschen: ungenutzt" }));
            await waitFor(() => expect(within(screen.getByTestId(`resource-${UNUSED_NET}`)).getByText("wartet auf Freigabe")).toBeInTheDocument());
            expected.push("Netz löschen: Netz ungenutzt gelöscht.");
          }
        }
        if (scenario !== "Anlegen") {
          fireEvent.click(await screen.findByRole("button", { name: "Container: ungenutzt" }));
          fireEvent.change(screen.getByRole("combobox"), { target: { value: "web" } });
          fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));
          await waitFor(() => expect(screen.queryByTestId("network-links-ungenutzt")).toBeNull());
          fireEvent.click(await screen.findByRole("button", { name: "Container: bridge" }));
          fireEvent.click(screen.getByRole("button", { name: "Trennen: alt" }));
          await waitFor(() => expect(screen.queryByTestId("network-links-bridge")).toBeNull());
          expected.push("Verbinden: web verbunden.", "Trennen: Abgelehnt: alt bleibt verbunden.");
        }
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        expect(screen.getByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig");
        settled = true;
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        await waitFor(() => {
          for (const message of expected) expect(screen.getByRole("status")).toHaveTextContent(message);
        });
        expect(screen.getByRole("status")).not.toHaveTextContent("Freigabe durch einen Admin nötig");
        await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
        for (const id of outcomes.keys()) expect(calls.filter((c) => c.url.endsWith(`/actions/${id}`))).toHaveLength(2);
        expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
      } finally {
        vi.useRealTimers();
      }
    },
  );
});

describe("Ergebnismeldungen beim Neuladen", () => {
  describe.each(["Netz anlegen", "Netz löschen", "Verbinden", "Trennen"] as const)("%s", (label) => {
    it.each<[string, boolean, ActionState, string]>([
      ["eigener Freigabe", false, { status: "succeeded", result: { output: "Aktion abgeschlossen." } }, "Aktion abgeschlossen."],
      ["fremder Freigabe", true, { status: "succeeded", result: { output: "Aktion abgeschlossen." } }, "Aktion abgeschlossen."],
      ["Fehler", true, { status: "failed", result: { error: "Server nicht erreichbar." } }, "Fehlgeschlagen: Server nicht erreichbar."],
      ["Ablehnung", true, { status: "denied", gate_decision: { rule: "user:reject", user_reason: "Wird noch benötigt." } }, "Abgelehnt: Wird noch benötigt."],
    ])("bleibt nach %s während des Ladens und bei Ladefehlern sichtbar", async (_, polled, outcome, expected) => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        if (polled) setDeck((p) => !p.startsWith("actions.approve"));
        let holdReload = false;
        let finishReload!: (response: Response) => void;
        const calls: Call[] = [];
        vi.stubGlobal("fetch", resourceFetch(calls, {
          "/networks": () => holdReload
            ? new Promise<Response>((resolve) => { finishReload = resolve; })
            : Response.json(LINKED),
          "/approve": () => Response.json({ id: "a1", ...outcome }),
          "/actions/a1": () => Response.json({ id: "a1", ...outcome }),
        }));
        render(<ResourceList hostId="h1" kind="networks" />);
        expect(screen.getByText(/Lade die Netze/)).toBeInTheDocument();
        await screen.findByTestId(`resource-${UNUSED_NET}`);
        holdReload = !polled;
        if (label === "Netz anlegen") {
          fireEvent.click(screen.getByRole("button", { name: label }));
          fireEvent.change(screen.getByRole("textbox", { name: "Name des Netzes" }), { target: { value: "intern" } });
          fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
        } else if (label === "Netz löschen") {
          fireEvent.click(screen.getByRole("button", { name: "Netz löschen: ungenutzt" }));
        } else {
          fireEvent.click(screen.getByRole("button", { name: `Container: ${label === "Verbinden" ? "ungenutzt" : "bridge"}` }));
          if (label === "Verbinden") fireEvent.change(screen.getByRole("combobox"), { target: { value: "web" } });
          fireEvent.click(screen.getByRole("button", { name: label === "Verbinden" ? "Verbinden" : "Trennen: alt" }));
        }
        const lists = () => calls.filter((c) => c.url.endsWith("/networks")).length;
        await waitFor(() => expect(lists()).toBe(2));
        if (polled) {
          await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Freigabe durch einen Admin nötig"));
          holdReload = true;
          await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
          expect(lists()).toBe(3);
        }
        const message = `${label}: ${expected}`;
        expect(screen.getByRole("status")).toHaveTextContent(message);
        expect(screen.getByTestId(`resource-${UNUSED_NET}`)).toBeInTheDocument();
        expect(screen.queryByText(/Lade die Netze/)).toBeNull();

        await act(async () => { finishReload(Response.json({ detail: "Docker antwortet nicht." }, { status: 502 })); });
        expect(screen.getByTestId("load-error")).toHaveTextContent("Docker antwortet nicht.");
        expect(screen.getByRole("status")).toHaveTextContent(message);
        fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
        expect(screen.queryByTestId("load-error")).toBeNull();
        expect(screen.getByRole("status")).toHaveTextContent(message);
        expect(screen.getByTestId(`resource-${UNUSED_NET}`)).toBeInTheDocument();

        await act(async () => { finishReload(Response.json({ ...LINKED, networks: [] })); });
        expect(screen.getByText("Auf diesem Server gibt es keine Netze.")).toBeInTheDocument();
        expect(screen.queryByTestId(`resource-${UNUSED_NET}`)).toBeNull();
        expect(screen.getByRole("status")).toHaveTextContent(message);
      } finally {
        cleanup();
        vi.useRealTimers();
      }
    });
  });
});

describe("ResourceList", () => {
  it("zeigt Images mit Nutzern und bietet nur Unbenutztes zum Löschen an", async () => {
    vi.stubGlobal("fetch", resourceFetch([]));
    render(<ResourceList hostId="h1" kind="images" />);
    const busybox = await screen.findByTestId(`resource-${BUSYBOX}`);
    expect(busybox).toHaveTextContent("benutzt von web");
    expect(within(busybox).queryByRole("button")).toBeNull();
    const own = screen.getByTestId(`resource-${OWN}`);
    expect(own).toHaveTextContent("von keinem Container benutzt");
    expect(within(own).getByRole("button", { name: "Image löschen: eigenes-image:1" })).toBeEnabled();
    const two = screen.getByTestId(`resource-${TWO_NAMES}`);
    expect(two).toHaveTextContent("Hat mehrere Namen");
    expect(within(two).queryByRole("button")).toBeNull();
    expect(busybox).toHaveTextContent("erstellt am 1.9.2026");
    const rollback = screen.getByTestId(`resource-${ROLLBACK}`);
    expect(rollback).toHaveTextContent("Bleibt als Rückweg für Nodvard Deck");
    expect(within(rollback).queryByRole("button")).toBeNull();
  });

  it.each([
    ["images", "Image löschen: eigenes-image:1", "docker.image_remove", { image: OWN }],
    ["networks", "Netz löschen: ungenutzt", "docker.network_remove", { network: UNUSED_NET }],
  ] as const)("schickt beim Löschen in %s genau die Kennung", async (kind, button, action, payload) => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ResourceList hostId="h1" kind={kind} />);
    fireEvent.click(await screen.findByRole("button", { name: button }));
    await waitFor(() => expect(calls.some((c) => c.url === `/api/v1/hosts/h1/actions/${action}`)).toBe(true));
    expect(calls.find((c) => c.url === `/api/v1/hosts/h1/actions/${action}`)?.body).toEqual(expect.objectContaining({ payload }));
  });

  it("zeigt nach einem Vorschlag ohne eigene Freigabe „wartet auf Freigabe“ statt eines zweiten Knopfs", async () => {
    setDeck((p) => p !== "actions.approve:high");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ResourceList hostId="h1" kind="volumes" />);
    fireEvent.click(await screen.findByRole("button", { name: "Volume löschen: leer" }));
    await screen.findByText(/Volume löschen: vorgeschlagen/);
    await waitFor(() => expect(within(screen.getByTestId("resource-leer")).getByText("wartet auf Freigabe")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Volume löschen: leer" })).toBeNull();
    expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
  });

  it("nimmt „wartet auf Freigabe“ weg, sobald der Vorschlag erledigt ist, und lädt neu", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      setDeck((p) => p !== "actions.approve:high");
      let status = "proposed";
      const calls: Call[] = [];
      vi.stubGlobal("fetch", resourceFetch(calls, { "/actions/a1": () => Response.json({ id: "a1", status }) }));
      render(<ResourceList hostId="h1" kind="volumes" />);
      fireEvent.click(await screen.findByRole("button", { name: "Volume löschen: leer" }));
      await waitFor(() => expect(within(screen.getByTestId("resource-leer")).getByText("wartet auf Freigabe")).toBeInTheDocument());
      const lists = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/volumes")).length;
      await vi.advanceTimersByTimeAsync(3100);
      expect(within(screen.getByTestId("resource-leer")).getByText("wartet auf Freigabe")).toBeInTheDocument();
      const before = lists();
      status = "succeeded";
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(screen.getByRole("button", { name: "Volume löschen: leer" })).toBeInTheDocument());
      expect(lists()).toBe(before + 1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("nennt den Grund, wenn das Löschen scheitert", async () => {
    vi.stubGlobal("fetch", resourceFetch([], {
      "/approve": () => Response.json({ id: "a1", status: "failed", result: { error: "An dem Netz hängen noch Container, auch gestoppte zählen: alt." } }),
    }));
    render(<ResourceList hostId="h1" kind="networks" />);
    fireEvent.click(await screen.findByRole("button", { name: "Netz löschen: ungenutzt" }));
    expect(await screen.findByRole("status")).toHaveTextContent("auch gestoppte zählen: alt.");
  });

  it("löscht ein Volume erst nach der Rückfrage und lädt die Liste neu", async () => {
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ResourceList hostId="h1" kind="volumes" />);
    fireEvent.click(await screen.findByRole("button", { name: "Volume löschen: leer" }));
    await screen.findByText("Volume löschen: Volume leer gelöscht.");
    expect(window.__nodvardDeck?.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Die Daten darin sind dann weg"), expect.objectContaining({ danger: true }));
    const post = calls.find((c) => c.method === "POST" && c.url.includes("/actions/docker.volume_remove"));
    expect(post?.url).toBe("/api/v1/hosts/h1/actions/docker.volume_remove");
    expect(post?.body).toEqual({ payload: { volume: "leer" }, reason: "Volume löschen über die Docker-Seite." });
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/volumes")).length).toBe(2));
  });

  it("schickt nach „Abbrechen“ nichts", async () => {
    setDeck(() => true, false);
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ResourceList hostId="h1" kind="volumes" />);
    fireEvent.click(await screen.findByRole("button", { name: "Volume löschen: leer" }));
    await waitFor(() => expect(window.__nodvardDeck?.confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("am Handy: jede Zeile als Karte, Knöpfe in eigener Zeile, ohne Knöpfe keine leere Zeile", async () => {
    vi.stubGlobal("fetch", resourceFetch([], { "/networks": () => Response.json(LINKED) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    const row = await screen.findByTestId(`resource-${UNUSED_NET}`);
    expect(row).toHaveClass("rounded-lg", "sm:border-0");
    const buttons = within(row).getByTestId(`resource-buttons-${UNUSED_NET}`);
    expect(buttons).toHaveClass("flex", "sm:contents");
    expect(within(buttons).getByRole("button", { name: "Container: ungenutzt" })).toBeInTheDocument();
    expect(within(buttons).getByRole("button", { name: "Netz löschen: ungenutzt" })).toBeInTheDocument();
    expect(screen.getByTestId(`resource-buttons-${"3".repeat(64)}`)).toHaveClass("hidden", "sm:contents");
  });

  it("zeigt ohne Server-Rechte keinen Löschen-Knopf", async () => {
    setDeck((p) => p !== "hosts.execute");
    vi.stubGlobal("fetch", resourceFetch([]));
    render(<ResourceList hostId="h1" kind="volumes" />);
    await screen.findByTestId("resource-leer");
    expect(screen.queryByRole("button", { name: /löschen/ })).toBeNull();
  });

  it("zeigt einen Ladefehler mit Grund statt einer leeren Liste", async () => {
    let broken = true;
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, {
      "/images": () => (broken ? Response.json({ detail: "Cannot connect to the Docker daemon" }, { status: 502 }) : Response.json(IMAGES)),
    }));
    render(<ResourceList hostId="h1" kind="images" />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Die Liste der Images ließ sich gerade nicht laden: Cannot connect to the Docker daemon");
    expect(screen.queryByText("Auf diesem Server liegen keine Images.")).toBeNull();
    broken = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    await screen.findByTestId(`resource-${OWN}`);
  });

  it("zeigt eingebaute Netze ohne Knopf und den Fehler, wenn nur die Netzliste fehlt", async () => {
    vi.stubGlobal("fetch", resourceFetch([]));
    const { unmount } = render(<ResourceList hostId="h1" kind="networks" />);
    const bridge = await screen.findByTestId(`resource-${"1".repeat(64)}`);
    expect(bridge).toHaveTextContent("eingebaut");
    expect(within(bridge).queryByRole("button", { name: /löschen/ })).toBeNull();
    expect(within(bridge).getByRole("button", { name: "Container: bridge" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Netz löschen: ungenutzt" })).toBeInTheDocument();
    unmount();

    vi.stubGlobal("fetch", resourceFetch([], { "/networks": () => Response.json({ host_id: "h1", networks: [], error: "permission denied" }) }));
    render(<ResourceList hostId="h1" kind="networks" />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("permission denied");
  });
});

describe("ContainersPage – Reiter", () => {
  it("öffnet über ?tab= die Liste des gewählten Servers und wechselt den Server", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=volumes&host=h2");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ContainersPage />);
    await screen.findByTestId("resources-volumes");
    expect(calls.some((c) => c.url === "/api/v1/ext/docker/hosts/h2/volumes")).toBe(true);
    expect(screen.getByRole("button", { name: "Volumes" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("textbox", { name: "Container suchen" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "h1" } });
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/ext/docker/hosts/h1/volumes")).toBe(true));
    expect(new URLSearchParams(window.location.search).get("host")).toBe("h1");
  });

  it("bietet auch einen Docker-Server ohne Container an", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=images");
    vi.stubGlobal("fetch", resourceFetch([]));
    render(<ContainersPage />);
    await screen.findByTestId("resources-images");
    expect(screen.getByRole("option", { name: "leerer-server" })).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("h1");
  });

  it("sagt ohne Docker-Server, was zu tun ist, und zeigt einen Ladefehler der Server-Liste", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=networks");
    vi.stubGlobal("fetch", resourceFetch([], { "/ext/docker/hosts": () => Response.json({ hosts: [] }) }));
    const { unmount } = render(<ContainersPage />);
    expect((await screen.findAllByText("Noch kein Server mit Docker")).length).toBeGreaterThan(0);
    unmount();

    vi.stubGlobal("fetch", resourceFetch([], { "/ext/docker/hosts": () => Response.json({ detail: "Datenbank gesperrt" }, { status: 503 }) }));
    render(<ContainersPage />);
    expect(await screen.findByTestId("load-error")).toHaveTextContent("Datenbank gesperrt");
  });

  it("braucht die Container-Liste nicht, um einen anderen Reiter zu zeigen", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=volumes");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls, { "/widgets/matrix": () => Response.json({ detail: "kaputt" }, { status: 500 }) }));
    render(<ContainersPage />);
    await screen.findByTestId("resources-volumes");
    expect(calls.some((c) => c.url.includes("/widgets/matrix") || c.url.includes("/ext/docker/stats"))).toBe(false);
  });

  it("zeigt bei nur einem Server keine Auswahl und bei unbekanntem Reiter die Container", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=networks");
    vi.stubGlobal("fetch", resourceFetch([], { "/ext/docker/hosts": () => Response.json({ hosts: [{ id: "h1", name: "pi" }] }) }));
    const { unmount } = render(<ContainersPage />);
    await screen.findByTestId("resources-networks");
    expect(screen.queryByRole("combobox")).toBeNull();
    unmount();

    window.history.replaceState({}, "", "/ext/docker/matrix?tab=gibtsnicht");
    vi.stubGlobal("fetch", resourceFetch([]));
    render(<ContainersPage />);
    await screen.findByRole("textbox", { name: "Container suchen" });
    const navigation = screen.getByRole("navigation", { name: "Bereiche der Docker-Seite" });
    expect(within(navigation).getByRole("button", { name: "Container" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("row-h1:web")).toBeInTheDocument();
    expect(screen.getByTestId("row-h2:db")).toBeInTheDocument();
    expect(screen.queryByTestId("resources-networks")).toBeNull();
  });

  it("wechselt zurück zu den Containern", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=images");
    vi.stubGlobal("fetch", resourceFetch([]));
    render(<ContainersPage />);
    await screen.findByTestId("resources-images");
    fireEvent.click(screen.getByRole("button", { name: "Container" }));
    await screen.findByRole("textbox", { name: "Container suchen" });
    expect(new URLSearchParams(window.location.search).get("tab")).toBeNull();
    expect(screen.queryByTestId("resources-images")).toBeNull();
  });

  it("zeigt für einen Server im Modus „Nur bedienen“ den Grund statt der Liste und nur die Knöpfe, die dort gehen", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=images&host=h2");
    const calls: Call[] = [];
    const hosts = { hosts: [{ id: "h1", name: "pi", mode: "full" }, { id: "h2", name: "nas", mode: "operate" }] };
    const services = [SERVICES[0], { ...SERVICES[1], compose_project: "shop", operate_only: true },
      { ...SERVICES[1], id: "h2:alt", name: "alt", container: "alt", state: "exited", operate_only: true }];
    vi.stubGlobal("fetch", resourceFetch(calls, {
      "/ext/docker/hosts": () => Response.json(hosts),
      "/widgets/matrix": () => Response.json({ data: services, meta: {} }),
    }));
    const { unmount } = render(<ContainersPage />);
    expect(await screen.findByTestId("operate-h2")).toHaveTextContent("Modus „Nur bedienen“");
    expect(screen.getByRole("option", { name: "nas (nur bedienen)" })).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/hosts/h2/images"))).toBe(false);
    unmount();

    window.history.replaceState({}, "", "/ext/docker/matrix");
    render(<ContainersPage />);
    await screen.findByTestId("operate-h2");
    const nas = screen.getByTestId("operate-h2").closest("section")!;
    const pi = screen.getByText("pi", { selector: "h3" }).closest("section")!;
    expect(within(nas).getByRole("button", { name: "Starten" })).toBeInTheDocument();
    expect(within(nas).getByRole("button", { name: "Stoppen" })).toBeInTheDocument();
    expect(within(nas).getAllByRole("button", { name: "Logs" })).toHaveLength(2);
    expect(within(nas).queryByRole("button", { name: "Löschen" })).toBeNull();
    expect(within(nas).queryByRole("link", { name: "Konsole" })).toBeNull();
    expect(within(nas).queryByRole("button", { name: "Docker-Speicher" })).toBeNull();
    expect(within(nas).getByText("shop").tagName).toBe("SPAN");
    expect(within(nas).queryByRole("button", { name: "shop" })).toBeNull();
    expect(within(pi).getByRole("link", { name: "Konsole" })).toBeInTheDocument();
    expect(within(pi).getByRole("button", { name: "Docker-Speicher" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Image-Updates prüfen" })).toBeInTheDocument();
    cleanup();

    window.history.replaceState({}, "", "/ext/docker/matrix?host=h2");
    render(<ContainersPage />);
    await screen.findByTestId("operate-h2");
    expect(screen.queryByRole("button", { name: "Image-Updates prüfen" })).toBeNull();
  });

  it("nennt im Modus „Nur bedienen“ die Markierung, mit der ein Server mehr darf", async () => {
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=images&host=h2");
    const hosts = { hosts: [{ id: "h1", name: "pi", mode: "full" }, { id: "h2", name: "nas", mode: "operate" }] };
    const settings = (tag: string) => () => Response.json({ values: { docker_host_tag: tag } });
    vi.stubGlobal("fetch", resourceFetch([], { "/ext/docker/hosts": () => Response.json(hosts), "/extensions/docker/settings": settings("voll") }));
    const { unmount } = render(<ContainersPage />);
    const note = await screen.findByTestId("operate-h2");
    await waitFor(() => expect(note).toHaveTextContent("Markierung „voll“"));
    expect(note).toHaveTextContent("Einstellungen, Server & Zugänge");
    expect(note).toHaveTextContent("nimmt den Zugang in die Gruppe docker auf");
    unmount();

    window.history.replaceState({}, "", "/ext/docker/matrix");
    const services = [SERVICES[0], { ...SERVICES[1], operate_only: true }];
    vi.stubGlobal("fetch", resourceFetch([], {
      "/ext/docker/hosts": () => Response.json(hosts),
      "/widgets/matrix": () => Response.json({ data: services, meta: {} }),
      "/extensions/docker/settings": settings("voll"),
    }));
    render(<ContainersPage />);
    const badge = await screen.findByTestId("operate-h2");
    await waitFor(() => expect(badge.title).toContain("Markierung „voll“"));
    expect(badge.title).toContain("Gruppe docker");
  });

  it("sagt bei Servern im Modus „Nur bedienen“, dass dort nicht auf neue Images geprüft wird", async () => {
    const operate = { ...SERVICES[1], operate_only: true };
    const checked = {
      data: { "h1:web": { image: "busybox:1.37", status: "current", reason: null, remote_digest: null, registry_at: null } },
      hosts: { h1: { checking: false, checked_at: "2026-10-16T10:00:00+00:00", error: null } },
    };
    vi.stubGlobal("fetch", resourceFetch([], {
      "/widgets/matrix": () => Response.json({ data: [SERVICES[0], operate], meta: {} }),
      "/ext/docker/image-updates": () => Response.json(checked),
    }));
    const { unmount } = render(<ContainersPage />);
    await waitFor(() => expect(screen.getByTestId("image-summary")).toHaveTextContent("alles aktuell · 1 Server im Modus „Nur bedienen“ nicht geprüft"));
    expect(within(screen.getByTestId("image-h2:db")).getByTitle("Auf Servern im Modus „Nur bedienen“ prüft Nodvard Deck nicht auf neue Images.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Image-Updates prüfen" })).toBeInTheDocument();
    unmount();

    vi.stubGlobal("fetch", resourceFetch([], { "/widgets/matrix": () => Response.json({ data: [operate], meta: {} }) }));
    render(<ContainersPage />);
    await screen.findByText("db");
    expect(screen.queryByRole("button", { name: "Image-Updates prüfen" })).toBeNull();
  });

  it("zeigt die Reiter nur mit Server-Rechten", async () => {
    setDeck((p) => p !== "hosts.execute");
    window.history.replaceState({}, "", "/ext/docker/matrix?tab=volumes");
    const calls: Call[] = [];
    vi.stubGlobal("fetch", resourceFetch(calls));
    render(<ContainersPage />);
    await screen.findByRole("textbox", { name: "Container suchen" });
    expect(screen.queryByRole("navigation", { name: "Bereiche der Docker-Seite" })).toBeNull();
    expect(calls.some((c) => c.url.includes("/volumes"))).toBe(false);
  });
});

it("Dock erklärt sein aufbewahrtes Rückweg-Image", async () => {
  window.__nodvardDeck.getProductName = () => "Nodvard Dock";
  resourceFetch([]);
  render(<ResourceList hostId="h1" kind="images" />);
  expect(await screen.findByText(/Rückweg für Nodvard Dock/)).toBeInTheDocument();
});

it("Dock kennzeichnet seine eigene Netzwerkverbindung", async () => {
  window.__nodvardDeck.getProductName = () => "Nodvard Dock";
  vi.stubGlobal("fetch", resourceFetch([], { "/networks": () => Response.json(LINKED) }));
  render(<ResourceList hostId="h1" kind="networks" />);
  fireEvent.click(await screen.findByRole("button", { name: "Container: dock" }));
  expect(screen.getByTestId("network-links-dock")).toHaveTextContent("Nodvard Dock selbst, bleibt verbunden");
});
