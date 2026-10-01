import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ServiceMatrixPage, type ImageResult } from "./ServiceMatrixPage";

const row = (host: string, host_id: string, name: string, over: Record<string, unknown> = {}) => ({
  id: `${host_id}:${name}`, name, host, host_id, container: name, is_self: false, state: "running", status: "Up 2 hours", tone: "good", url: null, ...over,
});
const SERVICES = [
  row("docker", "h1", "nginx"),
  row("docker", "h1", "redis"),
  row("docker", "h1", "postgres"),
  row("docker", "h1", "worker", { state: "exited" }),
  row("docker", "h1", "lattice-1", { is_self: true }),
  row("docker-zwei", "h2", "solo"),
];
const UPDATE: ImageResult = {
  image: "x:1", status: "update", reason: null, remote_digest: "sha256:" + "b".repeat(64), registry_at: "2026-09-30T06:30:00+00:00",
  apply: { mode: "compose", project: "web", service: "x" },
};
const HOSTS = {
  h1: { checking: false, checked_at: "2026-09-30T06:30:00+00:00", error: null },
  h2: { checking: false, checked_at: "2026-09-30T06:30:00+00:00", error: null },
};
const planOf = (container: string, over: Record<string, unknown> = {}) => ({
  ok: true, plan_id: `plan-${container}`, host_id: "h1", container, image: `${container}:1`, current_short: "111", remote_short: "222", registry_at: null, stale: false,
  project: "web", service: container, affected: [container], command: "docker compose pull", rollback: "docker tag", risk: "medium", warnings: [] as string[], ...over,
});

interface Call { url: string; method: string; body?: unknown }
interface Server {
  data: Record<string, ImageResult>;
  plans?: Record<string, unknown>;
  posts?: Record<string, { status: number; body: unknown }>;
}

function serverFetch(calls: Call[], server: Server, stats = { inFlight: 0, maxInFlight: 0 }) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
    if (url.includes("/widgets/matrix")) return json({ data: SERVICES, meta: {} });
    if (url.includes("/ext/service-matrix/stats")) return json({ data: {} });
    if (url.endsWith("/ext/service-matrix/image-updates")) return json({ data: server.data, hosts: HOSTS, cache_ttl_s: 21600, applying: {}, applied: {} });
    const m = url.match(/\/containers\/(h\d)\/([^/]+)\/image-update(\/plan)?$/);
    if (m) {
      const name = decodeURIComponent(m[2]);
      stats.inFlight += 1;
      stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
      await new Promise((r) => setTimeout(r, 5));
      stats.inFlight -= 1;
      if (m[3]) return json(server.plans?.[name] ?? planOf(name));
      const post = server.posts?.[name];
      return json(post?.body ?? { action_id: `a-${name}`, status: "proposed", risk: "medium", detail: null }, post?.status ?? 202);
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

async function open(server: Server, calls: Call[] = [], stats?: { inFlight: number; maxInFlight: number }) {
  vi.stubGlobal("fetch", serverFetch(calls, server, stats));
  render(<ServiceMatrixPage />);
  await screen.findByText("nginx");
  await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).not.toBe("–"));
  return calls;
}

const ALL = { "h1:nginx": UPDATE, "h1:redis": UPDATE, "h1:postgres": UPDATE };
const BUTTON = /^Alle Updates vorschlagen/;
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");

beforeEach(() => {
  window.history.replaceState({}, "", "/ext/service-matrix/matrix");
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn().mockResolvedValue(true),
    promptDialog: vi.fn(),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("ServiceMatrixPage -- Alle Updates vorschlagen", () => {
  it("Knopf erst ab 2 einspielbaren Containern auf einem Host, mit der richtigen Zahl", async () => {
    await open({ data: { "h1:nginx": UPDATE, "h2:solo": UPDATE } });
    // h1: nur nginx; h2: nur solo -> nirgends ein Knopf.
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
  });

  it("zählt nur Container, die auch einzeln 'Einspielen' hätten (nicht gestoppt, nicht Nodvard Deck selbst, nicht 'none', nicht aktuell)", async () => {
    await open({
      data: {
        ...ALL,
        "h1:worker": UPDATE, // gestoppt
        "h1:lattice-1": UPDATE, // Nodvard Deck selbst
        "h2:solo": UPDATE,
      },
    });
    expect(screen.getAllByRole("button", { name: BUTTON })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" })).toBeInTheDocument();
  });

  it("Container ohne Compose-Möglichkeit und aktuelle zählen nicht mit", async () => {
    await open({
      data: {
        "h1:nginx": UPDATE,
        "h1:redis": { ...UPDATE, apply: { mode: "none", kind: "portainer", why: "Portainer" } },
        "h1:postgres": { ...UPDATE, status: "current", apply: undefined },
      },
    });
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
  });

  it("ohne hosts.execute kein Knopf", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => p !== "hosts.execute");
    await open({ data: ALL });
    expect(screen.queryByRole("button", { name: BUTTON })).toBeNull();
  });

  it("Klick fragt nach: Container stehen in der Bestätigung, bei Abbruch wird nichts angelegt", async () => {
    (window.__lattice.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(false);
    const calls = await open({ data: ALL });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalledTimes(1));
    const message = (window.__lattice.confirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(message).toContain("nginx");
    expect(message).toContain("redis");
    expect(message).toContain("postgres");
    expect(message).not.toContain("lattice-1");
    expect(message).toContain("Aktionen");
    await waitFor(() => expect(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" })).not.toBeDisabled());
    expect(posts(calls)).toEqual([]);
    expect(screen.queryByText(/Vorschl(ag|äge) angelegt/)).toBeNull();
  });

  it("Datenbank-Container: die Bestätigung warnt davor", async () => {
    await open({
      data: ALL,
      plans: { postgres: planOf("postgres", { risk: "high", warnings: ["Datenbank-Container: Ein neues Image kann die Datenbank-Dateien umstellen. Vorher ein Backup machen."] }) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalled());
    const [message, options] = (window.__lattice.confirmDialog as ReturnType<typeof vi.fn>).mock.calls[0] as [string, { danger?: boolean }];
    expect(message).toMatch(/Datenbank/);
    expect(message).toMatch(/Backup/);
    expect(message).toContain("postgres");
    expect(options.danger).toBe(true);
  });

  it("ohne Datenbank keine Datenbank-Warnung", async () => {
    await open({ data: ALL });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    await waitFor(() => expect(window.__lattice.confirmDialog).toHaveBeenCalled());
    expect((window.__lattice.confirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0]).not.toMatch(/Datenbank/);
  });

  it("legt je Container einen Vorschlag an: nacheinander, mit der eigenen plan_id, ohne je freizugeben", async () => {
    const stats = { inFlight: 0, maxInFlight: 0 };
    const calls = await open({ data: ALL }, [], stats);
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    await screen.findByText(/3 Vorschläge angelegt/);

    const proposals = posts(calls);
    expect(proposals.map((c) => c.url)).toEqual([
      "/api/v1/ext/service-matrix/containers/h1/nginx/image-update",
      "/api/v1/ext/service-matrix/containers/h1/redis/image-update",
      "/api/v1/ext/service-matrix/containers/h1/postgres/image-update",
    ]);
    expect(proposals.map((c) => c.body)).toEqual([{ plan_id: "plan-nginx" }, { plan_id: "plan-redis" }, { plan_id: "plan-postgres" }]);
    // Nie zwei Anfragen gleichzeitig, und nichts wird freigegeben (auch nicht mit Freigabe-Recht).
    expect(stats.maxInFlight).toBe(1);
    expect(calls.some((c) => c.url.includes("/approve") || c.url.includes("/actions/"))).toBe(false);
    // Jeder Plan wird vor seinem eigenen Vorschlag geholt.
    const order = calls.filter((c) => c.url.includes("/image-update")).map((c) => `${c.method} ${c.url.split("/containers/")[1]}`);
    expect(order.indexOf("POST h1/nginx/image-update")).toBeGreaterThan(order.indexOf("GET h1/nginx/image-update/plan"));
    expect(order.indexOf("POST h1/redis/image-update")).toBeGreaterThan(order.indexOf("GET h1/redis/image-update/plan"));
  });

  it("Ergebniszeile mit Link zu „Aktionen“", async () => {
    await open({ data: ALL });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    const line = await screen.findByTestId("bulk-result-h1");
    expect(line.textContent).toContain("3 Vorschläge angelegt – freigeben unter „Aktionen“");
    expect(within(line).getByRole("link", { name: "Aktionen" })).toHaveAttribute("href", "/actions");
  });

  it("ein nicht einspielbarer Plan wird übersprungen, mit Grund genannt", async () => {
    const calls = await open({
      data: ALL,
      plans: { redis: { ok: false, kind: "files", reason: "Compose-Datei /opt/web/docker-compose.yml ist nicht lesbar." } },
    });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    const line = await screen.findByTestId("bulk-result-h1");
    await waitFor(() => expect(line.textContent).toContain("2 Vorschläge angelegt – freigeben unter „Aktionen“"));
    expect(line.textContent).toContain("Übersprungen");
    expect(line.textContent).toContain("redis");
    expect(line.textContent).toContain("Compose-Datei /opt/web/docker-compose.yml ist nicht lesbar.");
    expect(posts(calls).map((c) => c.url.split("/containers/")[1])).toEqual(["h1/nginx/image-update", "h1/postgres/image-update"]);
  });

  it("schlägt ein Vorschlag fehl (z. B. 409), geht es mit dem nächsten weiter und der Grund steht da", async () => {
    const calls = await open({
      data: ALL,
      posts: { nginx: { status: 409, body: { detail: "Der Stand hat sich geändert – bitte den Plan neu laden." } } },
    });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    const line = await screen.findByTestId("bulk-result-h1");
    await waitFor(() => expect(line.textContent).toContain("2 Vorschläge angelegt"));
    expect(line.textContent).toContain("nginx");
    expect(line.textContent).toContain("Der Stand hat sich geändert");
    expect(posts(calls)).toHaveLength(3);
  });

  it("wurde nichts angelegt, steht kein Link zu den Aktionen da, nur die Gründe", async () => {
    await open({
      data: ALL,
      plans: Object.fromEntries(["nginx", "redis", "postgres"].map((n) => [n, { ok: false, kind: "files", reason: `${n}: nicht lesbar` }])),
    });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    const line = await screen.findByTestId("bulk-result-h1");
    expect(line.textContent).toContain("Keine Vorschläge angelegt");
    expect(line.textContent).toContain("nginx: nicht lesbar");
    expect(within(line).queryByRole("link")).toBeNull();
    expect(window.__lattice.confirmDialog).not.toHaveBeenCalled();
  });

  it("danach wird der Image-Stand neu geladen", async () => {
    const calls = await open({ data: ALL });
    const imageGets = () => calls.filter((c) => c.url.endsWith("/ext/service-matrix/image-updates") && c.method === "GET").length;
    const before = imageGets();
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    await screen.findByText(/3 Vorschläge angelegt/);
    await waitFor(() => expect(imageGets()).toBeGreaterThan(before));
  });

  it("während der Lauf geht, ist der Knopf gesperrt und zeigt den Fortschritt", async () => {
    await open({ data: ALL });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    const busy = await screen.findByRole("button", { name: /Prüfe|Lege/ });
    expect(busy).toBeDisabled();
    await screen.findByText(/3 Vorschläge angelegt/);
    expect(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" })).not.toBeDisabled();
  });

  it("ein einzelner Vorschlag heißt „1 Vorschlag angelegt“", async () => {
    await open({ data: ALL, plans: { redis: { ok: false, kind: "x", reason: "weg" }, postgres: { ok: false, kind: "x", reason: "weg" } } });
    fireEvent.click(screen.getByRole("button", { name: "Alle Updates vorschlagen (3)" }));
    await screen.findByText(/1 Vorschlag angelegt – freigeben unter/);
  });
});
