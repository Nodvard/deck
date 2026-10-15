import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContainersPage } from "./ContainersPage";
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

function resourceFetch(calls: Call[], overrides: Record<string, () => Response> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    for (const [part, answer] of Object.entries(overrides)) if (url.includes(part)) return answer();
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
  window.__lattice = {
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
    expect(window.__lattice?.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("Die Daten darin sind dann weg"), expect.objectContaining({ danger: true }));
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
    await waitFor(() => expect(window.__lattice?.confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
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
    expect(within(bridge).queryByRole("button")).toBeNull();
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
    expect(screen.getByRole("button", { name: "Container" })).toHaveAttribute("aria-pressed", "true");
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
