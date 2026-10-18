/** Server-Filter (`?host=`): Zusammenfassung, "Prüfe Images …" und Knopf gelten nur für diesen Host. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContainersPage, environmentText, inStateFilter, restartsAreAProblem, scopeImages } from "./ContainersPage";

const SERVICES = [
  { id: "h1:nginx", name: "nginx", host: "raspberrypi", host_id: "h1", container: "nginx", is_self: false, state: "running", status: "Up", tone: "good", url: null },
  { id: "h2:db", name: "db", host: "docker", host_id: "h2", container: "db", is_self: false, state: "running", status: "Up", tone: "good", url: null },
];

const digest = (c: string) => "sha256:" + c.repeat(64);
const host = (over: Record<string, unknown> = {}) => ({ checking: false, checked_at: "2026-09-30T10:00:00+00:00", error: null, ...over });

function images(h2: Record<string, unknown> = {}) {
  return {
    data: {
      "h1:nginx": { image: "nginx:1.27", status: "current", reason: null, remote_digest: digest("a"), registry_at: null },
      "h2:db": { image: "postgres:16", status: "update", reason: null, remote_digest: digest("b"), registry_at: null },
    },
    hosts: { h1: host(), h2: host(h2) },
  };
}

function setup(body: unknown) {
  window.history.replaceState({}, "", "/ext/docker/matrix?host=h1");
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }));
    if (url.includes("/image-updates")) return new Response(JSON.stringify(body));
    if (url.includes("/stats")) return new Response(JSON.stringify({ data: {} }));
    throw new Error(url);
  }));
}

beforeEach(() => vi.unstubAllGlobals());

describe("Image-Zusammenfassung mit Server-Filter", () => {
  it("zählt nur den gefilterten Host (Update auf einem anderen Host zählt nicht mit)", async () => {
    setup(images());
    render(<ContainersPage />);
    await screen.findByText("nginx");
    expect(screen.queryByText("db")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("image-summary")).toBeInTheDocument());
    const text = screen.getByTestId("image-summary").textContent ?? "";
    expect(text).not.toContain("mit Update");
    expect(text).toContain("alles aktuell");
  });

  it("ein anderer Host, der nicht erreichbar ist, stört die Zeile nicht", async () => {
    setup(images({ error: "Keine Verbindung" }));
    render(<ContainersPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-summary")).toBeInTheDocument());
    expect(screen.getByTestId("image-summary").textContent).not.toContain("nicht erreichbar");
  });

  it("prüft gerade nur ein anderer Host: kein Hinweis und Knopf bleibt bedienbar", async () => {
    setup(images({ checking: true }));
    render(<ContainersPage />);
    await screen.findByText("nginx");
    const button = await screen.findByRole("button", { name: "Image-Updates prüfen" });
    expect(button).not.toBeDisabled();
    expect(screen.getByTestId("image-summary").textContent).not.toContain("Image-Quellen werden gefragt");
  });

  it("prüft der gefilterte Host selbst, steht der Hinweis da", async () => {
    const body = images();
    body.hosts.h1.checking = true;
    setup(body);
    render(<ContainersPage />);
    await screen.findByText("nginx");
    expect(await screen.findByRole("button", { name: "Prüfe Images …" })).toBeDisabled();
  });
});

describe("scopeImages", () => {
  it("ohne Filter bleibt alles", () => {
    const all = images();
    expect(scopeImages(all as never, null)).toBe(all);
  });
  it("mit Filter nur Host und Container mit dem Präfix", () => {
    const scoped = scopeImages({ ...images(), applying: { "h1:a": {}, "h2:b": {} } } as never, "h1")!;
    expect(Object.keys(scoped.data)).toEqual(["h1:nginx"]);
    expect(Object.keys(scoped.hosts)).toEqual(["h1"]);
    expect(Object.keys(scoped.applying ?? {})).toEqual(["h1:a"]);
  });
});

const MANY = [
  { id: "h1:shop-web", name: "shop-web", host: "docker", host_id: "h1", container: "shop-web", compose_project: "shop", state: "running", status: "Up 2 hours", tone: "good", url: null },
  { id: "h1:shop-db", name: "shop-db", host: "docker", host_id: "h1", container: "shop-db", compose_project: "shop", state: "running", status: "Up 2 hours (unhealthy)", tone: "good", url: null },
  { id: "h1:alt", name: "alt", host: "docker", host_id: "h1", container: "alt", state: "exited", status: "Exited (0) 3 days ago", tone: "neutral", url: null },
  { id: "h2:media", name: "media", host: "nas", host_id: "h2", container: "media", compose_project: "media", state: "restarting", status: "Restarting (1) 5 seconds ago", tone: "warn", url: null },
  { id: "h2:job", name: "job", host: "nas", host_id: "h2", container: "job", state: "exited", status: "Exited (1) 2 hours ago", tone: "neutral", url: null },
  { id: "h3:__error__", name: "pi", host: "pi", state: "error", status: "Nicht erreichbar: timeout", tone: "danger", url: null },
];

function setupMany(path: string, hosts: Record<string, unknown> = {}, restarts: Record<string, number> = {}) {
  window.history.replaceState({}, "", path);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: MANY, meta: {} }));
    if (url.includes("/image-updates")) return new Response(JSON.stringify({ data: {}, hosts: {} }));
    if (url.includes("/stats")) return new Response(JSON.stringify({ data: {}, hosts, restarts }));
    throw new Error(url);
  }));
}

const names = () => screen.queryAllByTestId(/^row-/).map((r) => r.getAttribute("data-testid")!.slice(4));
const pick = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("Filter nach Server, Stack und Zustand", () => {
  it("Stack, ohne Stack und Zustand wirken zusammen und stehen in der Adresszeile", async () => {
    setupMany("/ext/docker/matrix");
    render(<ContainersPage />);
    await screen.findByText("shop-web");

    pick("Stack", "shop");
    // Der nicht erreichbare Server bleibt bei jedem Stack- und Zustands-Filter stehen.
    expect(names()).toEqual(["h1:shop-web", "h1:shop-db", "h3:__error__"]);
    expect(new URLSearchParams(window.location.search).get("stack")).toBe("shop");

    pick("Zustand", "problems");
    expect(names()).toEqual(["h1:shop-db", "h3:__error__"]);

    pick("Stack", "-");
    expect(names()).toEqual(["h2:job", "h3:__error__"]);
    pick("Zustand", "stopped");
    expect(names()).toEqual(["h1:alt", "h2:job", "h3:__error__"]);

    fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
    expect(names()).toHaveLength(6);
    expect(window.location.search).toBe("");
    expect(screen.queryByRole("button", { name: "Filter entfernen" })).toBeNull();
  });

  it("Mit Problemen: startet neu, ungesund und nicht erreichbare Server", async () => {
    setupMany("/ext/docker/matrix?state=problems");
    render(<ContainersPage />);
    await screen.findByText("shop-db");
    expect(names()).toEqual(["h1:shop-db", "h2:media", "h2:job", "h3:__error__"]);
  });

  it("ein nicht erreichbarer Server bleibt mit seinem Grund sichtbar, wenn man nach ihm filtert", async () => {
    setupMany("/ext/docker/matrix?host=h3");
    render(<ContainersPage />);
    expect(await screen.findByText(/Nicht erreichbar: timeout/)).toBeInTheDocument();
    expect(names()).toEqual(["h3:__error__"]);
    expect(screen.getByLabelText("Server")).toHaveValue("h3");
  });

  it("die Stacks folgen dem Server, und ein Serverwechsel nimmt den Stack-Filter heraus", async () => {
    setupMany("/ext/docker/matrix?host=h1&stack=shop");
    render(<ContainersPage />);
    await screen.findByText("shop-web");
    const stacks = () => [...(screen.getByLabelText("Stack") as HTMLSelectElement).options].map((o) => o.value);
    expect(stacks()).toEqual(["", "shop", "-"]);

    pick("Server", "h2");
    expect(new URLSearchParams(window.location.search).get("stack")).toBeNull();
    expect(names()).toEqual(["h2:media", "h2:job"]);
    expect(stacks()).toEqual(["", "media", "-"]);
  });

  it("nennt je Server, worauf sich CPU und RAM beziehen", async () => {
    setupMany("/ext/docker/matrix", {
      h1: { virtualization: "lxc", cpus: 2, mem_total: 4 * 1024 ** 3, docker_version: "29.8.2" },
      h2: { virtualization: "none", cpus: 4, mem_total: null, docker_version: null },
    });
    render(<ContainersPage />);
    expect((await screen.findByTestId("environment-h1")).textContent).toBe("im LXC-Container, Grenze 2 CPU · 4.0 GB RAM");
    expect(screen.getByTestId("environment-h1").title).toContain("nicht für den ganzen Rechner darunter");
    expect(screen.getByTestId("environment-h2").textContent).toBe("4 CPU");
  });
});

describe("Neustarts", () => {
  it("stehen beim Status; ab drei mit frischem Start hervorgehoben und unter „Mit Problemen“", async () => {
    setupMany("/ext/docker/matrix", {}, { "h1:shop-web": 1, "h1:alt": 7, "h2:media": 9 });
    render(<ContainersPage />);
    expect((await screen.findByTestId("restarts-h2:media")).textContent).toBe("· 9× neu gestartet");
    expect(screen.getByTestId("restarts-h2:media").className).toContain("text-amber-300");
    // Gestoppt seit Tagen: der Zähler steht noch, ist aber kein aktuelles Problem.
    expect(screen.getByTestId("restarts-h1:alt").className).not.toContain("text-amber-300");
    expect(screen.queryByTestId("restarts-h1:shop-db")).toBeNull();

    pick("Zustand", "problems");
    expect(names()).toContain("h2:media");
    expect(names()).not.toContain("h1:alt");
  });
});

describe("restartsAreAProblem", () => {
  it("nur mit frischem Start", () => {
    expect(restartsAreAProblem(5, "Up 12 seconds")).toBe(true);
    expect(restartsAreAProblem(5, "Up About a minute")).toBe(true);
    expect(restartsAreAProblem(5, "Up 40 minutes")).toBe(true);
    expect(restartsAreAProblem(5, "Restarting (1) 3 seconds ago")).toBe(true);
    expect(restartsAreAProblem(5, "Up About an hour")).toBe(false);
    expect(restartsAreAProblem(5, "Up 3 weeks")).toBe(false);
    expect(restartsAreAProblem(2, "Up 12 seconds")).toBe(false);
    expect(restartsAreAProblem(undefined, "Up 12 seconds")).toBe(false);
  });
});

describe("inStateFilter und environmentText", () => {
  it("gestoppt umfasst auch erstellte und pausierte, läuft nur laufende", () => {
    expect(inStateFilter({ state: "paused", status: "Up 1 hour (Paused)" }, "stopped")).toBe(true);
    expect(inStateFilter({ state: "created", status: "Created" }, "stopped")).toBe(true);
    expect(inStateFilter({ state: "restarting", status: "Restarting" }, "running")).toBe(false);
    expect(inStateFilter({ state: "dead", status: "Dead" }, "problems")).toBe(true);
  });

  it("abgestürzt zählt zu den Problemen, ein normales Stoppen nicht", () => {
    expect(inStateFilter({ state: "exited", status: "Exited (1) 2 hours ago" }, "problems")).toBe(true);
    expect(inStateFilter({ state: "exited", status: "Exited (139) 1 minute ago" }, "problems")).toBe(true);
    for (const code of [0, 130, 137, 143]) {
      expect(inStateFilter({ state: "exited", status: `Exited (${code}) 3 days ago` }, "problems")).toBe(false);
    }
  });

  it("VM und Container", () => {
    expect(environmentText({ virtualization: "vm", cpus: 4, mem_total: 8 * 1024 ** 3, docker_version: null }).text).toBe("VM mit 4 CPU · 8.0 GB RAM");
    expect(environmentText({ virtualization: "container", cpus: 1, mem_total: null, docker_version: null }).text).toBe("im Container, 1 CPU");
  });
});
