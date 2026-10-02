import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { navigateTo } from "../../../_shared/frontend/src/testShell";

import type { AppliedState, ApplyingState } from "./ImageUpdateApply";
import { ImageUpdateBadge, ServiceMatrixPage, statusText, summarizeImages, type ImageResult } from "./ServiceMatrixPage";

const SERVICES = [
  { id: "h1:nginx", name: "nginx", host: "docker", host_id: "h1", container: "nginx", is_self: false, state: "running", status: "Up 2 hours", tone: "good", url: "http://10.0.0.5:8080" },
  { id: "h1:worker", name: "worker", host: "docker", host_id: "h1", container: "worker", is_self: false, state: "exited", status: "Exited (1)", tone: "neutral", url: null },
  { id: "h1:lattice-1", name: "lattice-1", host: "docker", host_id: "h1", container: "lattice-1", is_self: true, state: "running", status: "Up 1 day", tone: "good", url: null },
  { id: "h2:__error__", name: "⚠ docker-lxc", host: "docker-lxc", state: "error", status: "Nicht erreichbar: timeout", tone: "danger", url: null },
];

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/ext/service-matrix/widgets/matrix")) {
      return new Response(JSON.stringify({ data: SERVICES, meta: {} }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
  });
}

beforeEach(() => {
  // Offene Logs und Filter stehen in der Adresszeile -- jeder Test beginnt ohne.
  window.history.replaceState({}, "", "/ext/service-matrix/matrix");
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn(),
    promptDialog: vi.fn(),
    hasPermission: vi.fn().mockReturnValue(true),
  };
});

describe("ServiceMatrixPage", () => {
  it("gruppiert Container nach Host, auch die Fehler-Kachel bleibt bei ihrem echten Host", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ServiceMatrixPage />);

    await screen.findByText("nginx");
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    // "docker" und "docker-lxc" jeweils EINMAL -- nicht die Fehlermeldung als
    // eigene, dritte Gruppe (das war der eigentliche Fund im Backend).
    expect(headings).toEqual(["docker", "docker-lxc"]);
  });

  it("zeigt Zustand, Statustext und einen Oeffnen-Link je Container", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ServiceMatrixPage />);

    await screen.findByText("nginx");
    expect(screen.getByText("Läuft seit 2 Stunden")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Öffnen" })).toHaveAttribute("href", "http://10.0.0.5:8080");
  });

  it("zeigt die Fehlermeldung einer nicht erreichbaren Quelle als Statustext", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ServiceMatrixPage />);

    await screen.findByText(/Nicht erreichbar: timeout/);
  });

  it("zeigt einen Hinweis, wenn gar keine Container gefunden wurden", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ data: [], meta: {} }), { status: 200 })));
    render(<ServiceMatrixPage />);

    await screen.findByText("Noch kein Server mit Docker");
  });

  it("zeigt eine Fehlermeldung, wenn der Endpunkt selbst scheitert", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));
    render(<ServiceMatrixPage />);

    await screen.findByText(/Fehler: HTTP 500/);
  });

  it.each([
    ["ganz weg (Neustart nach einem Deploy)", () => { throw new TypeError("Failed to fetch"); }],
    ["hinter einem Proxy nicht da (502)", () => new Response("<html>Bad Gateway</html>", { status: 502 })],
  ])("Server %s: verständliche Meldung statt „Failed to fetch“/„HTTP 502“", async (_label, reply) => {
    vi.stubGlobal("fetch", vi.fn(async () => reply()));
    render(<ServiceMatrixPage />);

    expect(await screen.findByText("Fehler: Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.")).toBeInTheDocument();
  });

  it("Handy: jede Zeile wird zur Karte (Tabelle ohne Spaltenköpfe, Werte mit Beschriftung, Knöpfe in der Karte)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    const row = screen.getByTestId("row-h1:nginx");
    // Ab 768 px Breite bleibt es die Tabelle; darunter liegen die Zellen untereinander in einer Karte.
    expect(row.className).toContain("max-md:flex");
    expect(row.className).toContain("max-md:flex-wrap");
    const table = row.closest("table")!;
    expect(table.className).toContain("max-md:block");
    expect(table.querySelector("thead")!.className).toContain("max-md:hidden");
    expect(screen.getByTestId("cpu-h1:nginx")).toHaveAttribute("data-label", "CPU");
    expect(screen.getByTestId("mem-h1:nginx")).toHaveAttribute("data-label", "RAM");
    // Die Knöpfe sind Teil derselben Karte (kein seitliches Wischen nötig) und am Handy größer.
    const details = within(row).getByRole("button", { name: "Details" });
    expect(details.className).toContain("max-md:py-2");
    expect(within(row).getByRole("button", { name: "Logs" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Neustart" })).toBeInTheDocument();
  });

  it("Docker-Status auf Deutsch, mit dem Original als Tooltip", async () => {
    const services = [
      { ...SERVICES[0], status: "Up 3 hours (healthy)" },
      { ...SERVICES[1], status: "Exited (0) 10 hours ago" },
    ];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("/widgets/matrix") ? new Response(JSON.stringify({ data: services, meta: {} })) : new Response("{}", { status: 404 })));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    const up = screen.getByText("Läuft seit 3 Stunden (gesund)");
    expect(up).toHaveAttribute("title", "Up 3 hours (healthy)");
    expect(screen.getByText("Beendet (Code 0) vor 10 Stunden")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("hours ago");
  });

  it("ohne Container gibt es keinen Knopf „Image-Updates prüfen“ (er würde nichts bewirken)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ data: [], meta: {} }), { status: 200 })));
    render(<ServiceMatrixPage />);
    await screen.findByText("Noch kein Server mit Docker");
    expect(screen.queryByRole("button", { name: "Image-Updates prüfen" })).toBeNull();
    expect(screen.getByRole("button", { name: "Aktualisieren" })).toBeInTheDocument();
  });

  it("eine Suche ohne Treffer sagt es", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    fireEvent.change(screen.getByLabelText("Container suchen"), { target: { value: "gibt-es-nicht" } });
    expect(screen.getByTestId("no-match").textContent).toBe("Kein Container passt zu deiner Suche.");
    fireEvent.change(screen.getByLabelText("Container suchen"), { target: { value: "" } });
    expect(screen.queryByTestId("no-match")).toBeNull();
  });

  it("zeigt Docker-Zustaende auf Deutsch, die Farbe bleibt am Ton", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect(screen.getAllByText("läuft")).toHaveLength(2);
    expect(screen.getByText("gestoppt")).toBeInTheDocument();
    expect(screen.queryByText("running")).toBeNull();
  });
});

describe("ServiceMatrixPage -- Container-Verwaltung", () => {
  function managedFetch(calls: { url: string; method: string; body?: unknown }[], logBody?: ReadableStream<Uint8Array>) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      if (url.includes("/ext/service-matrix/widgets/matrix")) {
        return new Response(JSON.stringify({ data: SERVICES, meta: {} }), { status: 200 });
      }
      if (url.includes("/actions/container.") && method === "POST") {
        return new Response(JSON.stringify({ id: "a1", status: "proposed", risk: "medium", result: {} }), { status: 202 });
      }
      if (url.endsWith("/actions/a1/approve")) {
        return new Response(JSON.stringify({ id: "a1", status: "succeeded", result: {} }), { status: 200 });
      }
      if (url.includes("/logs?")) {
        (init?.signal as AbortSignal | undefined)?.addEventListener("abort", () => calls.push({ url, method: "ABORT" }));
        return new Response(logBody ?? "", { status: 200, headers: { "Content-Type": "text/plain" } });
      }
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
  }

  it("bietet je Zustand die passenden Knoepfe an", async () => {
    vi.stubGlobal("fetch", managedFetch([]));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    // nginx + lattice-1 laufen -> Neustart/Stoppen; worker ist gestoppt -> Starten.
    expect(screen.getAllByRole("button", { name: "Neustart" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Stoppen" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Starten" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Logs" })).toHaveLength(3);
  });

  it("Neustart geht ueber den Kern-Aktionsweg (Nutzer als Ausloeser) und wird bei Berechtigung freigegeben", async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", managedFetch(calls));
    (window.__lattice.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");

    fireEvent.click(screen.getAllByRole("button", { name: "Neustart" })[0]);
    await screen.findByText('"nginx" neu gestartet.');

    const post = calls.find((c) => c.url.includes("/actions/container.restart"));
    expect(post?.url).toBe("/api/v1/hosts/h1/actions/container.restart");
    expect((post?.body as { payload: unknown }).payload).toEqual({ container: "nginx" });
    expect(calls.some((c) => c.url.endsWith("/actions/a1/approve"))).toBe(true);
  });

  it("ohne Freigabe-Berechtigung bleibt es beim Vorschlag", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", managedFetch(calls));
    (window.__lattice.hasPermission as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<ServiceMatrixPage />);
    await screen.findByText("worker");

    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    await screen.findByText(/vorgeschlagen – Freigabe durch einen Admin/);
    expect(calls.some((c) => c.url.endsWith("/approve"))).toBe(false);
  });

  it("Nodvard Deck selbst laesst sich hier nicht stoppen", async () => {
    vi.stubGlobal("fetch", managedFetch([]));
    render(<ServiceMatrixPage />);
    await screen.findByText("lattice-1");
    const stopButtons = screen.getAllByRole("button", { name: "Stoppen" });
    expect(stopButtons.filter((b) => (b as HTMLButtonElement).disabled)).toHaveLength(1);
  });

  it("Live-Logs: Zeilen erscheinen laufend, ANSI-Farben entfernt, Filter wirkt, Schliessen bricht den Strom ab", async () => {
    const encoder = new TextEncoder();
    let push: (text: string) => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (text) => controller.enqueue(encoder.encode(text));
      },
    });
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", managedFetch(calls, stream));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");

    fireEvent.click(screen.getAllByRole("button", { name: "Logs" })[0]);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/containers/h1/nginx/logs?tail=200"))).toBe(true));

    push("2026-09-24T07:00:00Z \u001b[32mstarted\u001b[0m\n2026-09-24T07:00:01Z GET /health 200\n");
    const panel = await screen.findByTestId("log-panel");
    await waitFor(() => expect(panel.querySelector("pre")?.textContent).toContain("started"));
    expect(panel.querySelector("pre")?.textContent).not.toContain("\u001b");

    push("2026-09-24T07:00:02Z worker crashed\n");
    await waitFor(() => expect(panel.querySelector("pre")?.textContent).toContain("worker crashed"));

    fireEvent.change(screen.getByLabelText("Logs filtern"), { target: { value: "health" } });
    expect(panel.querySelector("pre")?.textContent).toBe("2026-09-24T07:00:01Z GET /health 200");

    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "ABORT")).toBe(true));
    expect(screen.queryByTestId("log-panel")).toBeNull();
  });

  it("zeigt Image, CPU und RAM -- unbekannter Speicher als 'n. v.' statt 0 B", async () => {
    const services = [
      { ...SERVICES[0], image: "nginx:1.27" },
      { ...SERVICES[2], image: "lattice:latest" },
    ];
    const stats = {
      "h1:nginx": { cpu_percent: 1.5, mem_used: 45 * 1024 * 1024, mem_limit: 4 * 1024 ** 3 },
      "h1:lattice-1": { cpu_percent: 0.29, mem_used: null, mem_limit: null },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: services, meta: {} }));
        if (url.endsWith("/ext/service-matrix/stats")) return new Response(JSON.stringify({ data: stats }));
        throw new Error(`Unerwarteter Fetch: ${url}`);
      }),
    );
    render(<ServiceMatrixPage />);
    expect(await screen.findByText("nginx:1.27")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("cpu-h1:nginx").textContent).toBe("1.5 %"));
    expect(screen.getByTestId("mem-h1:nginx").textContent).toBe("45 MB");
    expect(screen.getByTestId("mem-h1:lattice-1").textContent).toBe("n. v.");
  });

  it("waehrend der Messung steht '…' statt '–'", async () => {
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => (release = resolve));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }));
        if (url.endsWith("/ext/service-matrix/stats")) return pending;
        throw new Error(`Unerwarteter Fetch: ${url}`);
      }),
    );
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect(screen.getByTestId("cpu-h1:nginx").textContent).toBe("…");
    expect(screen.getByTestId("mem-h1:nginx").textContent).toBe("…");

    release(new Response(JSON.stringify({ data: { "h1:nginx": { cpu_percent: 2, mem_used: null, mem_limit: null } } })));
    await waitFor(() => expect(screen.getByTestId("cpu-h1:nginx").textContent).toBe("2.0 %"));
    // Gestoppter Container ohne Messwert: jetzt "–", nicht mehr "…".
    expect(screen.getByTestId("cpu-h1:worker").textContent).toBe("–");
  });

  it("Sprung von der Server-Seite (?host=): nur die Container dieses Hosts, Filter entfernbar", async () => {
    window.history.replaceState({}, "", "/ext/service-matrix/matrix?host=h1");
    try {
      vi.stubGlobal("fetch", mockFetch());
      render(<ServiceMatrixPage />);
      await screen.findByText("nginx");
      expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["docker"]);
      expect(screen.getByTestId("host-filter").textContent).toContain("Nur docker");
      fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
      expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["docker", "docker-lxc"]);
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("Filter und offene Logs folgen der Adresszeile: derselbe Link wirkt wieder, nachdem man sie geändert hat", async () => {
    window.history.replaceState({}, "", "/ext/service-matrix/matrix?host=h1");
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", managedFetch(calls));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    const hosts = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);

    fireEvent.click(screen.getByRole("button", { name: "Filter entfernen" }));
    expect(hosts()).toEqual(["docker", "docker-lxc"]);
    expect(window.location.search).toBe("");
    // Für den Router ist das dieselbe Adresse wie beim Öffnen -- die Seite muss trotzdem filtern.
    navigateTo("/ext/service-matrix/matrix?host=h1");
    expect(hosts()).toEqual(["docker"]);

    // Logs öffnen schreibt ?logs= (ohne neuen Verlaufseintrag), Schließen nimmt nur das wieder heraus.
    fireEvent.click(screen.getAllByRole("button", { name: "Logs" })[0]);
    expect(await screen.findByTestId("log-panel")).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("logs")).toBe("h1:nginx");
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    expect(screen.queryByTestId("log-panel")).toBeNull();
    expect(window.location.search).toBe("?host=h1");

    navigateTo("/ext/service-matrix/matrix?host=h1&logs=h1%3Anginx");
    expect(await screen.findByTestId("log-panel")).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.method === "GET" && c.url.includes("/containers/h1/nginx/logs?")).length).toBe(2));
  });

  function dockerFetch(calls: { url: string; method: string }[]) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url.includes("/ext/service-matrix/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }), { status: 200 });
      if (url.includes("/containers/h1/nginx/inspect")) {
        return new Response(JSON.stringify({
          image: "nginx:1.27", image_id: "cccccccccccc", created: null, started_at: "2026-09-24T07:00:00Z", finished_at: null,
          running: true, exit_code: null, oom_killed: false, health: "healthy", restart_policy: "unless-stopped", restart_count: 2,
          privileged: false, network_mode: "bridge", memory_limit: null,
          ports: [{ container: "80/tcp", published: ["8080"] }],
          mounts: [{ type: "volume", source: "web_data", destination: "/usr/share/nginx/html", read_only: true }],
          networks: [{ name: "bridge", ip: "172.17.0.2" }],
          env_keys: ["API_TOKEN", "TZ"], compose: { project: "web", service: "nginx", working_dir: "/opt/web" },
        }), { status: 200 });
      }
      if (url.includes("/ext/service-matrix/hosts/h1/docker")) {
        return new Response(JSON.stringify({
          disk: [{ type: "Build Cache", label: "Build-Cache", total: 388, active: 0, size: 10_150_000_000, reclaimable: 9_113_000_000 }],
          images: [{ id: "bbb222", name: null, dangling: true, size: 1_200_000_000, created: "3 weeks ago", in_use: false }],
        }), { status: 200 });
      }
      if (url.includes("/hosts/h1/actions/docker.prune_build_cache") && method === "POST") {
        return new Response(JSON.stringify({ id: "p1", status: "proposed", risk: "medium" }), { status: 202 });
      }
      if (url.endsWith("/actions/p1/approve")) {
        return new Response(JSON.stringify({ id: "p1", status: "succeeded", result: { output: "Freigegeben: 9.113GB" } }), { status: 200 });
      }
      if (url.includes("/ext/service-matrix/stats")) return new Response(JSON.stringify({ data: {} }), { status: 200 });
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
  }

  it("Details wie in Portainer: Ports, Speicher, Compose, Env nur mit Namen", async () => {
    vi.stubGlobal("fetch", dockerFetch([]));
    render(<ServiceMatrixPage />);
    const row = (await screen.findByText("nginx")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Details" }));
    const panel = await screen.findByTestId("inspect-h1:nginx");
    expect(panel.textContent).toContain("8080 → 80/tcp");
    expect(panel.textContent).toContain("Volume web_data → /usr/share/nginx/html (nur lesen)");
    expect(panel.textContent).toContain("außer manuell gestoppt · 2× neu gestartet");
    expect(panel.textContent).toContain("web / nginx · /opt/web");
    expect(panel.textContent).toContain("Umgebungsvariablen (2)API_TOKEN, TZ");
  });

  it("Docker-Speicher: Belegung, frei machbar, Aufräumen über das Gate mit Rückfrage", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", dockerFetch(calls));
    const confirmDialog = vi.fn().mockResolvedValue(true);
    window.__lattice.confirmDialog = confirmDialog;
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    fireEvent.click(screen.getByRole("button", { name: "Docker-Speicher" }));
    const storage = await screen.findByTestId("storage-h1");
    expect(storage.textContent).toContain("Build-Cache (0/388 aktiv)");
    expect(storage.textContent).toContain("8.5 GB frei machbar");
    expect(storage.textContent).toContain("ohne Namen (bbb222)");

    fireEvent.click(within(storage).getByRole("button", { name: "Build-Cache leeren" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Build-Cache leeren: Freigegeben: 9.113GB"));
    expect(confirmDialog.mock.calls[0][0]).toContain("dauert dann deutlich länger");
    expect(calls.filter((c) => c.url.includes("/hosts/h1/docker")).length).toBe(2);
  });

  it("ohne hosts.execute kein Docker-Speicher-Knopf", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => p !== "hosts.execute");
    vi.stubGlobal("fetch", dockerFetch([]));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect(screen.queryByRole("button", { name: "Docker-Speicher" })).toBeNull();
  });
});

describe("ServiceMatrixPage -- Image-Updates", () => {
  const HOST_IDLE = { h1: { checking: false, checked_at: null, error: null } };
  const DIGEST = "sha256:" + "b".repeat(64);
  const LOCAL_REASON = "Selbst gebautes Image – es gibt dafür keine Registry, Updates kommen über den eigenen Build (beim Dashboard: Deploy).";
  const RESULTS: Record<string, ImageResult> = {
    "h1:nginx": { image: "nginx:1.27", status: "update", reason: null, remote_digest: DIGEST, registry_at: "2026-09-30T06:30:00+00:00" },
    "h1:lattice-1": { image: "lattice:latest", status: "local", reason: LOCAL_REASON, remote_digest: null, registry_at: null },
  };
  const CHECKED = { h1: { checking: false, checked_at: "2026-09-30T06:30:00+00:00", error: null } };

  /** Der Server merkt sich den Stand; `respond` darf ihn je Aufruf aendern. */
  function imageFetch(calls: { url: string; method: string }[], state: { data: Record<string, ImageResult>; hosts: Record<string, unknown> }, onPost?: () => void) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }), { status: 200 });
      if (url.includes("/ext/service-matrix/image-updates/check") && method === "POST") {
        onPost?.();
        return new Response(JSON.stringify({ ...state, cache_ttl_s: 21600 }), { status: 202 });
      }
      if (url.endsWith("/ext/service-matrix/image-updates")) return new Response(JSON.stringify({ ...state, cache_ttl_s: 21600 }), { status: 200 });
      if (url.includes("/ext/service-matrix/stats")) return new Response(JSON.stringify({ data: {} }), { status: 200 });
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
  }

  it("Badge je Zustand: aktuell, Update verfügbar, selbst gebaut, nicht prüfbar mit Grund, noch nicht geprüft", () => {
    const { rerender } = render(<ImageUpdateBadge result={{ image: "nginx:1", status: "current", reason: null, remote_digest: DIGEST, registry_at: null }} />);
    expect(screen.getByText("aktuell")).toBeInTheDocument();
    rerender(<ImageUpdateBadge result={RESULTS["h1:nginx"]} />);
    const update = screen.getByText("Update verfügbar");
    expect(update.className).toContain("amber");
    expect(update.getAttribute("title")).toContain("bbbbbbbbbbbb");
    rerender(<ImageUpdateBadge result={RESULTS["h1:lattice-1"]} />);
    const local = screen.getByText("selbst gebaut");
    expect(local.className).toContain("bg-white/10"); // neutrales Grau wie "nicht prüfbar", keine Warnfarbe
    expect(local.className).not.toContain("amber");
    expect(local.className).not.toContain("red");
    expect(screen.getByText(LOCAL_REASON)).toBeInTheDocument(); // als kleiner Hinweis darunter
    expect(screen.queryByText("nicht prüfbar")).toBeNull();
    expect(screen.queryByText(/docker login/)).toBeNull();
    rerender(<ImageUpdateBadge result={{ image: "nico/private:1", status: "unknown", reason: "Registry verweigert den Zugriff (privates oder dort nicht vorhandenes Image) -- ggf. auf dem Host per docker login anmelden", remote_digest: null, registry_at: null }} />);
    expect(screen.getByText("nicht prüfbar")).toBeInTheDocument();
    expect(screen.queryByText("selbst gebaut")).toBeNull();
    expect(screen.getByText(/per docker login anmelden/)).toBeInTheDocument();
    rerender(<ImageUpdateBadge />);
    expect(screen.getByText("–")).toBeInTheDocument();
    rerender(<ImageUpdateBadge checking />);
    expect(screen.getByText("prüft …")).toBeInTheDocument();
  });

  it("zeigt beim Öffnen den gespeicherten Stand, ohne selbst zu prüfen", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", imageFetch(calls, { data: RESULTS, hosts: CHECKED }));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).toBe("Update verfügbar"));
    expect(screen.getByTestId("image-h1:lattice-1").textContent).toContain("selbst gebaut");
    expect(screen.getByTestId("image-h1:lattice-1").textContent).toContain("Selbst gebautes Image");
    expect(screen.getByTestId("image-h1:lattice-1").textContent).not.toContain("nicht prüfbar");
    // Gestoppter Container: noch nie geprüft; Fehler-Kachel des Hosts: gar kein Badge.
    expect(screen.getByTestId("image-h1:worker").textContent).toBe("–");
    expect(screen.getByTestId("image-h2:__error__").textContent).toBe("");
    expect(screen.getByTestId("image-summary").textContent).toContain("1 mit Update · 1 selbst gebaut");
    expect(screen.getByTestId("image-summary").textContent).not.toContain("nicht prüfbar");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("ohne Stand bleibt die Spalte bei '–' und die Seite funktioniert", async () => {
    vi.stubGlobal("fetch", mockFetch()); // kennt /image-updates nicht -> Fehler beim Nachfragen
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect(screen.getByTestId("image-h1:nginx").textContent).toBe("–");
    expect(screen.queryByTestId("image-summary")).toBeNull();
  });

  it("Knopf startet die Prüfung im Hintergrund und fragt nach, bis sie fertig ist", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const calls: { url: string; method: string }[] = [];
      const state: { data: Record<string, ImageResult>; hosts: Record<string, unknown> } = { data: {}, hosts: HOST_IDLE };
      vi.stubGlobal("fetch", imageFetch(calls, state, () => { state.hosts = { h1: { checking: true, checked_at: null, error: null } }; }));
      render(<ServiceMatrixPage />);
      await screen.findByText("nginx");

      fireEvent.click(screen.getByRole("button", { name: "Image-Updates prüfen" }));
      const busy = await screen.findByRole("button", { name: "Prüfe Images …" });
      expect((busy as HTMLButtonElement).disabled).toBe(true);
      expect(calls.find((c) => c.method === "POST")?.url).toBe("/api/v1/ext/service-matrix/image-updates/check");
      expect(screen.getByTestId("image-h1:nginx").textContent).toBe("prüft …");

      // Der Server ist fertig -- beim nächsten Nachfragen erscheinen die Ergebnisse.
      state.data = RESULTS;
      state.hosts = CHECKED;
      await act(async () => { vi.advanceTimersByTime(2100); });
      await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).toBe("Update verfügbar"));
      expect(screen.getByRole("button", { name: "Image-Updates prüfen" })).toBeEnabled();
      const gets = calls.filter((c) => c.url.endsWith("/image-updates") && c.method === "GET").length;
      // Danach kein weiteres Nachfragen mehr.
      await act(async () => { vi.advanceTimersByTime(10_000); });
      expect(calls.filter((c) => c.url.endsWith("/image-updates") && c.method === "GET").length).toBe(gets);
    } finally {
      vi.useRealTimers();
    }
  });

  it("'Neu abfragen' überspringt den Zwischenspeicher, der Host-Filter begrenzt die Prüfung", async () => {
    window.history.replaceState({}, "", "/ext/service-matrix/matrix?host=h1");
    try {
      const calls: { url: string; method: string }[] = [];
      vi.stubGlobal("fetch", imageFetch(calls, { data: RESULTS, hosts: CHECKED }));
      render(<ServiceMatrixPage />);
      await screen.findByText("nginx");
      fireEvent.click(await screen.findByRole("button", { name: "Neu abfragen" }));
      await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
      expect(calls.find((c) => c.method === "POST")?.url).toBe("/api/v1/ext/service-matrix/image-updates/check?host_id=h1&force=true");
    } finally {
      window.history.replaceState({}, "", "/");
    }
  });

  it("zeigt Fehler des Hosts und der Anfrage", async () => {
    const hosts = { h1: { checking: false, checked_at: "2026-09-30T06:30:00+00:00", error: "Nicht erreichbar: timeout" } };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }));
        if (url.includes("/image-updates/check") && init?.method === "POST") return new Response(JSON.stringify({ detail: "Kein Docker-Host." }), { status: 404 });
        if (url.endsWith("/image-updates")) return new Response(JSON.stringify({ data: RESULTS, hosts }));
        return new Response(JSON.stringify({ data: {} }));
      }),
    );
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    expect((await screen.findByTestId("image-error-h1")).textContent).toBe("Image-Prüfung: Nicht erreichbar: timeout");
    // Der letzte gute Stand bleibt trotzdem sichtbar.
    expect(screen.getByTestId("image-h1:nginx").textContent).toBe("Update verfügbar");

    fireEvent.click(screen.getByRole("button", { name: "Image-Updates prüfen" }));
    await screen.findByText("Image-Prüfung: Kein Docker-Host.");
  });

  describe("Zusammenfassung", () => {
    const at = "2026-09-30T06:30:00+00:00";
    const res = (status: ImageResult["status"], extra: Partial<ImageResult> = {}): ImageResult => ({
      image: "x:1", status, reason: null, remote_digest: null, registry_at: null, ...extra,
    });
    const okHost = { checking: false, checked_at: at, error: null };

    it("'alles aktuell' nur, wenn etwas geprüft wurde und nichts offen blieb", () => {
      const text = summarizeImages({ data: { "h1:a": res("current"), "h1:b": res("current") }, hosts: { h1: okHost } }, false);
      expect(text).toContain("alles aktuell");
    });

    it("Abruflimit: nichts ist verifiziert -- kein 'alles aktuell'", () => {
      const limited = res("unknown", { reason: "Abruflimit der Registry erreicht" });
      const text = summarizeImages({ data: { "h1:a": limited, "h1:b": limited }, hosts: { h1: okHost } }, false);
      expect(text).not.toContain("alles aktuell");
      expect(text).toContain("2 nicht prüfbar");
    });

    it("nur ein Host-Fehler und keine Ergebnisse: 'Prüfung nicht möglich'", () => {
      // Der Server meldet ohne früheren Erfolg kein `checked_at`.
      const text = summarizeImages({ data: {}, hosts: { h1: { checking: false, checked_at: null, error: "Nicht erreichbar: timeout" } } }, false);
      expect(text).toBe("Image-Updates · Prüfung nicht möglich");
      // Auch mit Zeitstempel (älterer Server) kein 'alles aktuell'.
      const withTime = summarizeImages({ data: {}, hosts: { h1: { checking: false, checked_at: at, error: "Nicht erreichbar: timeout" } } }, false);
      expect(withTime).toContain("Prüfung nicht möglich");
      expect(withTime).not.toContain("alles aktuell");
    });

    it("gemischt: zählt jeden Zustand, Host-Fehler und ältere Antworten", () => {
      const text = summarizeImages(
        {
          data: { "h1:a": res("update"), "h1:b": res("current"), "h1:c": res("current", { stale: true }), "h1:d": res("unknown") },
          hosts: { h1: okHost, h2: { checking: false, checked_at: null, error: "Nicht erreichbar: weg" } },
        },
        false,
      );
      expect(text).toContain("1 mit Update · 2 aktuell · 1 nicht prüfbar · 1 mit älterer Antwort der Registry · 1 Server nicht erreichbar");
      expect(text).not.toContain("alles aktuell");
    });

    it("selbst gebaut ist kein Problem: 'alles aktuell' bleibt, die Zahl steht dahinter", () => {
      const text = summarizeImages({ data: { "h1:a": res("current"), "h1:b": res("current"), "h1:lattice": res("local") }, hosts: { h1: okHost } }, false);
      expect(text).toContain("alles aktuell · 1 selbst gebaut");
      expect(text).not.toContain("nicht prüfbar");
    });

    it("nur selbst gebaute Container: nichts wurde verglichen, also kein 'alles aktuell', aber auch keine Warnung", () => {
      const text = summarizeImages({ data: { "h1:lattice": res("local"), "h1:mine": res("local") }, hosts: { h1: okHost } }, false);
      expect(text).toMatch(/ · 2 selbst gebaut$/);
      expect(text).not.toContain("alles aktuell");
      expect(text).not.toContain("nicht prüfbar");
    });

    it("gemischt mit selbst gebaut: Update, aktuell, selbst gebaut, dann die echten Probleme", () => {
      const data: Record<string, ImageResult> = { "h1:lattice": res("local") };
      for (let i = 0; i < 11; i++) data[`h1:u${i}`] = res("update");
      data["h1:c1"] = res("current");
      data["h1:c2"] = res("current");
      expect(summarizeImages({ data, hosts: { h1: okHost } }, false)).toMatch(/ · 11 mit Update · 2 aktuell · 1 selbst gebaut$/);
      data["h1:unsure"] = res("unknown");
      const text = summarizeImages({ data, hosts: { h1: okHost } }, false);
      expect(text).toMatch(/ · 11 mit Update · 2 aktuell · 1 selbst gebaut · 1 nicht prüfbar$/);
    });

    it("selbst gebaut hebt ein Problem nicht auf: mit Abruflimit oder Host-Fehler kein 'alles aktuell'", () => {
      const limited = summarizeImages({ data: { "h1:a": res("current"), "h1:lattice": res("local"), "h1:b": res("unknown") }, hosts: { h1: okHost } }, false);
      expect(limited).not.toContain("alles aktuell");
      expect(limited).toContain("1 aktuell · 1 selbst gebaut · 1 nicht prüfbar");
      const hostDown = summarizeImages({ data: { "h1:a": res("current"), "h1:lattice": res("local") }, hosts: { h1: okHost, h2: { checking: false, checked_at: null, error: "weg" } } }, false);
      expect(hostDown).not.toContain("alles aktuell");
      expect(hostDown).toContain("1 aktuell · 1 selbst gebaut · 1 Server nicht erreichbar");
    });

    it("ohne laufende Container und ohne Fehler, während der Prüfung und ganz ohne Stand", () => {
      expect(summarizeImages({ data: {}, hosts: { h1: okHost } }, false)).toContain("keine laufenden Container");
      expect(summarizeImages({ data: {}, hosts: { h1: okHost } }, true)).toContain("werden gefragt");
      expect(summarizeImages(null, false)).toBeNull();
      expect(summarizeImages({ data: {}, hosts: {} }, false)).toBeNull();
    });

    it("die Seite zeigt bei einem reinen Host-Fehler keine Entwarnung", async () => {
      const hosts = { h1: { checking: false, checked_at: null, error: "Nicht erreichbar: timeout" } };
      vi.stubGlobal("fetch", imageFetch([], { data: {}, hosts }));
      render(<ServiceMatrixPage />);
      await screen.findByText("nginx");
      await waitFor(() => expect(screen.getByTestId("image-summary").textContent).toContain("Prüfung nicht möglich"));
      expect(screen.getByTestId("image-summary").textContent).not.toContain("alles aktuell");
      expect(screen.getByTestId("image-error-h1").textContent).toContain("timeout");
    });
  });

  it("'Aktualisieren' lädt auch den Stand der Image-Updates neu", async () => {
    const calls: { url: string; method: string }[] = [];
    const state: { data: Record<string, ImageResult>; hosts: Record<string, unknown> } = { data: {}, hosts: HOST_IDLE };
    vi.stubGlobal("fetch", imageFetch(calls, state));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).toBe("–"));
    const getsBefore = calls.filter((c) => c.url.endsWith("/image-updates") && c.method === "GET").length;

    // Der Tagesjob (oder ein anderer Tab) hat inzwischen geprüft.
    state.data = RESULTS;
    state.hosts = CHECKED;
    fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
    await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).toBe("Update verfügbar"));
    expect(calls.filter((c) => c.url.endsWith("/image-updates") && c.method === "GET").length).toBe(getsBefore + 1);
  });

  it("Hinweis in der Spalte: laufend ohne Ergebnis heißt 'noch nicht geprüft', gestoppt heißt 'wird nicht geprüft'", async () => {
    vi.stubGlobal("fetch", imageFetch([], { data: {}, hosts: CHECKED }));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-summary")).toBeInTheDocument());
    expect(within(screen.getByTestId("image-h1:nginx")).getByText("–").getAttribute("title")).toBe("Noch nicht geprüft.");
    expect(within(screen.getByTestId("image-h1:worker")).getByText("–").getAttribute("title")).toBe("Gestoppte Container werden nicht geprüft.");
  });

  it("Badge: älterer Stand wird gekennzeichnet, mit Grund und Zeit der letzten Antwort", () => {
    const stale: ImageResult = {
      image: "nginx:1.27", status: "update", reason: "Abruflimit der Registry erreicht. Angezeigt wird die letzte Antwort der Registry.",
      remote_digest: DIGEST, registry_at: "2026-09-30T06:30:00+00:00", stale: true,
    };
    const { rerender } = render(<ImageUpdateBadge result={stale} />);
    expect(screen.getByText("Update verfügbar")).toBeInTheDocument();
    const note = screen.getByText("alter Stand");
    expect(note.getAttribute("title")).toContain("Abruflimit");
    expect(note.getAttribute("title")).toContain("Letzte Antwort der Registry");
    rerender(<ImageUpdateBadge result={{ ...stale, status: "current" }} />);
    expect(screen.getByText("aktuell")).toBeInTheDocument();
    expect(screen.getByText("alter Stand")).toBeInTheDocument();
    rerender(<ImageUpdateBadge result={{ ...stale, stale: false }} />);
    expect(screen.queryByText("alter Stand")).toBeNull();
  });

  it("ohne hosts.execute nur lesen: Badges ja, Knöpfe nein", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => p !== "hosts.execute");
    vi.stubGlobal("fetch", imageFetch([], { data: RESULTS, hosts: CHECKED }));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).toBe("Update verfügbar"));
    expect(screen.queryByRole("button", { name: "Image-Updates prüfen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Neu abfragen" })).toBeNull();
  });
});


describe("ServiceMatrixPage -- Image-Update einspielen", () => {
  const DIGEST = "sha256:" + "b".repeat(64);
  const HOST_CHECKED = { h1: { checking: false, checked_at: "2026-09-30T06:30:00+00:00", error: null } };
  const UPDATE: ImageResult = {
    image: "nginx:1.27", status: "update", reason: null, remote_digest: DIGEST, registry_at: "2026-09-30T06:30:00+00:00",
    apply: { mode: "compose", project: "web", service: "nginx" },
  };
  const COMMAND = "docker compose --ansi never -p web --project-directory /opt/web -f /opt/web/docker-compose.yml pull nginx && docker compose --ansi never -p web --project-directory /opt/web -f /opt/web/docker-compose.yml up -d --no-deps --no-build nginx";
  const PLAN = {
    ok: true, plan_id: "0123456789abcdef", host_id: "h1", container: "nginx", image: "nginx:1.27", current_image_id: "sha256:" + "1".repeat(64),
    current_short: "111111111111", remote_digest: DIGEST, remote_short: "bbbbbbbbbbbb", registry_at: "2026-09-30T06:30:00+00:00", stale: false,
    project: "web", service: "nginx", affected: ["nginx"], command: COMMAND,
    rollback: "docker tag lattice-rollback/nginx:previous nginx:1.27\ndocker compose up -d --no-deps --no-build nginx", risk: "medium", warnings: [] as string[],
  };

  interface Server {
    data: Record<string, ImageResult>;
    applying?: Record<string, ApplyingState>;
    applied?: Record<string, AppliedState>;
    plan?: unknown;
    planStatus?: number;
    post?: { status: number; body: unknown };
    approve?: unknown;
    action?: unknown;
    /** Wird beim Freigeben aufgerufen (der Server beginnt dann zu arbeiten). */
    onApprove?: () => void;
  }
  interface Call { url: string; method: string; body?: unknown }

  function serverFetch(calls: Call[], server: Server) {
    return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : undefined });
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
      if (url.includes("/widgets/matrix")) return json({ data: SERVICES, meta: {} });
      if (url.includes("/ext/service-matrix/stats")) return json({ data: {} });
      if (url.endsWith("/ext/service-matrix/image-updates")) {
        return json({ data: server.data, hosts: HOST_CHECKED, cache_ttl_s: 21600, applying: server.applying ?? {}, applied: server.applied ?? {} });
      }
      if (url.endsWith("/image-update/plan")) return json(server.plan ?? PLAN, server.planStatus ?? 200);
      if (url.endsWith("/image-update") && method === "POST") return json(server.post?.body ?? { action_id: "a1", status: "proposed", risk: "medium", detail: null }, server.post?.status ?? 202);
      if (url.endsWith("/actions/a1/approve")) server.onApprove?.();
      if (url.endsWith("/actions/a1/approve")) return json(server.approve ?? { id: "a1", status: "succeeded", result: { success: true, output: "„nginx“ aktualisiert: nginx:1.27\nImage-ID alt 111 → neu 999" } });
      if (url.endsWith("/actions/a1")) return json(server.action ?? { id: "a1", status: "succeeded", result: { success: true, output: "„nginx“ aktualisiert: nginx:1.27" } });
      throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
    });
  }

  async function open(server: Server, calls: Call[] = []) {
    vi.stubGlobal("fetch", serverFetch(calls, server));
    render(<ServiceMatrixPage />);
    await screen.findByText("nginx");
    await waitFor(() => expect(screen.getByTestId("image-h1:nginx").textContent).not.toBe("–"));
    return calls;
  }

  it("Knopf nur bei 'Update verfügbar' laufender Container, die nicht Nodvard Deck selbst sind", async () => {
    await open({ data: { "h1:nginx": UPDATE, "h1:lattice-1": { ...UPDATE, image: "lattice:latest" }, "h1:worker": UPDATE } });
    // nginx: ja; lattice-1 (Nodvard Deck selbst) und worker (gestoppt): nein.
    expect(screen.getAllByRole("button", { name: "Einspielen" })).toHaveLength(1);
    expect(within(screen.getByTestId("image-action-h1:nginx")).getByRole("button", { name: "Einspielen" })).toBeInTheDocument();
    expect(screen.queryByTestId("image-action-h1:lattice-1")).toBeNull();
    expect(screen.queryByTestId("image-action-h1:worker")).toBeNull();
  });

  it.each([
    ["aktuell", { status: "current" }],
    ["nicht prüfbar", { status: "unknown", reason: "Registry nicht erreichbar" }],
    ["selbst gebaut", { status: "local", reason: "Selbst gebautes Image" }],
  ])("kein Knopf bei %s", async (_label, over) => {
    await open({ data: { "h1:nginx": { ...UPDATE, ...over, apply: undefined } as ImageResult } });
    expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
    expect(screen.queryByTestId("image-action-h1:nginx")).toBeNull();
  });

  it("ohne hosts.execute kein Knopf", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => p !== "hosts.execute");
    await open({ data: { "h1:nginx": UPDATE } });
    expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
  });

  it("wenn es sich hier nicht einspielen lässt, steht der Grund statt des Knopfes da", async () => {
    const why = "Von Portainer verwaltet – bitte in Portainer aktualisieren.";
    await open({ data: { "h1:nginx": { ...UPDATE, apply: { mode: "none", kind: "portainer", why } } } });
    expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
    expect(screen.getByTestId("image-action-h1:nginx").textContent).toBe(why);
  });

  it("ohne Angabe zur Einspielbarkeit (Hinweis fehlt) bleibt der Knopf -- die Übersicht entscheidet", async () => {
    await open({ data: { "h1:nginx": { ...UPDATE, apply: undefined } } });
    expect(screen.getByRole("button", { name: "Einspielen" })).toBeInTheDocument();
  });

  it("Klick zeigt zuerst die Übersicht: Image, Versionen, Befehl, Warnung und Rückweg -- ohne etwas auszulösen", async () => {
    const calls = await open({ data: { "h1:nginx": UPDATE } });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));

    await screen.findByText("Update für „nginx“ einspielen");
    expect(calls.find((c) => c.url.endsWith("/image-update/plan"))?.url).toBe("/api/v1/ext/service-matrix/containers/h1/nginx/image-update/plan");
    expect(screen.getByText(/Image-ID 111111111111 · neue Version bei der Registry \(Prüfsumme bbbbbbbbbbbb\), Stand/)).toBeInTheDocument();
    expect(screen.queryByText(/Jetzt:/)).toBeNull();
    expect(screen.getByText("web/nginx")).toBeInTheDocument();
    expect(screen.getByText(/ist dabei kurz nicht erreichbar/)).toBeInTheDocument();
    expect(screen.getByText(COMMAND).tagName).toBe("PRE");
    expect(screen.getByText(/docker tag lattice-rollback\/nginx:previous nginx:1.27/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jetzt einspielen" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    // Abbrechen schliesst die Übersicht wieder.
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(screen.queryByText("Update für „nginx“ einspielen")).toBeNull();
  });

  it("Datenbank: die Warnung steht in der Übersicht, mehrere Container werden genannt", async () => {
    await open({
      data: { "h1:nginx": UPDATE },
      plan: { ...PLAN, risk: "high", affected: ["nginx", "nginx-2"], warnings: ["Datenbank-Container: Ein neues Image kann die Datenbank-Dateien auf eine neue Version umstellen. Vorher ein Backup machen.", "Kein fester Versions-Tag („latest“)."] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    expect(await screen.findByText(/Vorher ein Backup machen/)).toBeInTheDocument();
    expect(screen.getByText("nginx, nginx-2")).toBeInTheDocument();
    expect(screen.getByText(/Kein fester Versions-Tag/)).toBeInTheDocument();
  });

  it("ok:false zeigt den Grund und keinen Knopf zum Einspielen", async () => {
    await open({ data: { "h1:nginx": UPDATE }, plan: { ok: false, kind: "files", reason: "Compose-Datei /opt/web/docker-compose.yml ist für den SSH-Benutzer nicht lesbar." } });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    expect(await screen.findByText(/nicht lesbar/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt einspielen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Vorschlagen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    expect(screen.queryByText(/nicht lesbar/)).toBeNull();
  });

  it("ein Fehler beim Laden der Übersicht wird angezeigt", async () => {
    await open({ data: { "h1:nginx": UPDATE }, plan: { detail: "Host nicht erreichbar: timeout" }, planStatus: 502 });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    expect(await screen.findByText(/Übersicht nicht abrufbar: .*timeout/)).toBeInTheDocument();
  });

  it("mit Freigabe-Recht: senden mit der plan_id, selbst freigeben, Ergebnis zeigen, danach alles neu laden", async () => {
    const calls = await open({ data: { "h1:nginx": UPDATE } });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));

    expect(await screen.findByText("Fertig: „nginx“ aktualisiert: nginx:1.27")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/image-update"));
    expect(post?.url).toBe("/api/v1/ext/service-matrix/containers/h1/nginx/image-update");
    expect(post?.body).toEqual({ plan_id: "0123456789abcdef" });
    expect(calls.some((c) => c.url.endsWith("/actions/a1/approve") && c.method === "POST")).toBe(true);
    expect(screen.getByText(/Protokoll und Rückweg/)).toBeInTheDocument();
    // Matrix und Image-Stand werden neu geladen (Container hat ein neues Image).
    const before = calls.filter((c) => c.url.includes("/widgets/matrix")).length;
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/widgets/matrix")).length).toBeGreaterThan(before - 1));
    expect(calls.filter((c) => c.url.endsWith("/ext/service-matrix/image-updates")).length).toBeGreaterThan(1);
  });

  it("nach dem Freigeben wird der Stand gleich neu geladen: 'Update läuft' steht da, der Knopf in der Zeile ist weg", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const server: Server = { data: { "h1:nginx": UPDATE }, approve: { id: "a1", status: "executing", result: {} } };
      server.onApprove = () => { server.applying = { "h1:nginx": { phase: "pull", started_at: "2026-09-30T07:00:00+00:00", run_id: "imgupd_0123456789abcdef" } }; };
      const calls = await open(server);
      const imageGets = () => calls.filter((c) => c.url.endsWith("/ext/service-matrix/image-updates") && c.method === "GET").length;
      fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
      const before = imageGets();
      fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));

      // Ohne das 30-Sekunden-Nachladen der Seite: gleich nach dem Freigeben.
      await waitFor(() => expect(screen.getByTestId("image-action-h1:nginx").textContent).toBe("Update läuft – lädt Image …"));
      expect(imageGets()).toBeGreaterThan(before);
      expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("schon nach dem Anlegen des Vorschlags (vor der Freigabe) wird der Stand neu geladen", async () => {
    const calls = await open({ data: { "h1:nginx": UPDATE } });
    const imageGets = () => calls.filter((c) => c.url.endsWith("/ext/service-matrix/image-updates") && c.method === "GET").length;
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    const before = imageGets();
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));
    await screen.findByText(/Fertig:/);
    expect(imageGets()).toBeGreaterThan(before);
  });

  it("dauert die Aktion, wird nachgefragt (alle 3 Sekunden), bis sie fertig ist", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const server: Server = { data: { "h1:nginx": UPDATE }, approve: { id: "a1", status: "executing", result: {} } };
      const calls = await open(server);
      fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
      fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));
      expect(await screen.findByText(/Läuft …/)).toBeInTheDocument();
      expect(calls.some((c) => c.url.endsWith("/actions/a1") && c.method === "GET")).toBe(false);
      await act(async () => { vi.advanceTimersByTime(3100); });
      expect(await screen.findByText(/Fertig: „nginx“ aktualisiert/)).toBeInTheDocument();
      expect(calls.filter((c) => c.url.endsWith("/actions/a1") && c.method === "GET")).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ohne Freigabe-Recht bleibt es bei 'Vorschlagen' -- kein Freigeben", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => !p.startsWith("actions.approve"));
    const calls = await open({ data: { "h1:nginx": UPDATE } });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    expect(await screen.findByText("Ein Admin muss unter „Aktionen“ freigeben.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt einspielen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Vorschlagen" }));

    expect(await screen.findByText("Vorgeschlagen – Freigabe durch einen Admin nötig, siehe „Aktionen“.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/approve"))).toBe(false);
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/image-update"))).toBe(true);
  });

  it("bei hohem Risiko wird das Freigabe-Recht für 'hoch' geprüft", async () => {
    window.__lattice.hasPermission = vi.fn((p: string) => p === "hosts.execute" || p === "actions.approve:medium");
    await open({ data: { "h1:nginx": UPDATE }, plan: { ...PLAN, risk: "high" } });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    expect(await screen.findByRole("button", { name: "Vorschlagen" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt einspielen" })).toBeNull();
  });

  it("Fehlschlag: die Meldung und das Protokoll stehen da", async () => {
    await open({
      data: { "h1:nginx": UPDATE },
      approve: { id: "a1", status: "failed", result: { success: false, error: "Abruflimit der Registry erreicht. Der Container läuft unverändert weiter.", output: "„nginx“: Abruflimit …\n--- Protokoll (Ende) ---\ntoomanyrequests" } },
    });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));
    expect(await screen.findByText(/Fehlgeschlagen: Abruflimit der Registry erreicht/)).toBeInTheDocument();
    expect(screen.getByText(/toomanyrequests/).tagName).toBe("PRE");
  });

  it("von der Sperrliste oder Freigabe abgelehnt: der Grund steht da", async () => {
    await open({
      data: { "h1:nginx": UPDATE },
      approve: { id: "a1", status: "denied", gate_decision: { rule: "deny_pattern:x", detail: "passt auf ein gesperrtes Muster" }, result: {} },
    });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));
    expect(await screen.findByText(/Gesperrt: passt auf ein gesperrtes Muster/)).toBeInTheDocument();
  });

  it("409 (Stand hat sich geändert): Meldung und 'Neu laden' lädt die Übersicht neu", async () => {
    const calls = await open({ data: { "h1:nginx": UPDATE }, post: { status: 409, body: { detail: "Der Stand hat sich geändert – bitte den Plan neu laden." } } });
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt einspielen" }));
    expect(await screen.findByText("Der Stand hat sich geändert – bitte den Plan neu laden.")).toBeInTheDocument();
    expect(screen.queryByText(/Fertig:/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Neu laden" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/image-update/plan"))).toHaveLength(2));
    expect(await screen.findByRole("button", { name: "Jetzt einspielen" })).toBeInTheDocument();
  });

  it("läuft ein Update, zeigt die Spalte den Schritt und fragt nach, bis es fertig ist", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const server: Server = { data: { "h1:nginx": UPDATE }, applying: { "h1:nginx": { phase: "pull", started_at: "2026-09-30T07:00:00+00:00", run_id: "imgupd_0123456789abcdef" } } };
      const calls = await open(server);
      expect(screen.getByTestId("image-action-h1:nginx").textContent).toBe("Update läuft – lädt Image …");
      expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
      expect(screen.getByTestId("image-summary").textContent).toContain("1 Update wird eingespielt");

      server.applying = { "h1:nginx": { phase: "up", started_at: "2026-09-30T07:00:00+00:00", run_id: "imgupd_0123456789abcdef" } };
      await act(async () => { vi.advanceTimersByTime(2100); });
      await waitFor(() => expect(screen.getByTestId("image-action-h1:nginx").textContent).toBe("Update läuft – erstellt Container neu …"));

      // Fertig: der Container ist aktuell, das Ergebnis bleibt sichtbar.
      server.applying = {};
      server.data = { "h1:nginx": { ...UPDATE, status: "current", apply: undefined } };
      server.applied = { "h1:nginx": { ok: true, summary: "„nginx“ aktualisiert: nginx:1.27", finished_at: "2026-09-30T07:01:00+00:00" } };
      await act(async () => { vi.advanceTimersByTime(2100); });
      await waitFor(() => expect(screen.getByTestId("image-action-h1:nginx").textContent).toMatch(/^✓ eingespielt \d{1,2}:\d{2}/));
      expect(screen.getByTestId("image-h1:nginx").textContent).toBe("aktuell");
      const gets = calls.filter((c) => c.url.endsWith("/image-updates")).length;
      await act(async () => { vi.advanceTimersByTime(10_000); });
      expect(calls.filter((c) => c.url.endsWith("/image-updates")).length).toBe(gets);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ein fehlgeschlagenes Update steht in der Spalte, der Knopf bleibt für einen neuen Versuch", async () => {
    await open({
      data: { "h1:nginx": UPDATE },
      applied: { "h1:nginx": { ok: false, summary: "„nginx“: Neu erstellen fehlgeschlagen", finished_at: "2026-09-30T07:01:00+00:00" } },
    });
    const cell = screen.getByTestId("image-action-h1:nginx");
    expect(cell.textContent).toMatch(/Update fehlgeschlagen \d{1,2}:\d{2}.*Einspielen/);
    expect(within(cell).getByText(/Update fehlgeschlagen/)).toHaveAttribute("title", "„nginx“: Neu erstellen fehlgeschlagen");
  });

  it("Zusammenfassung nennt laufende Updates", () => {
    const applying = { "h1:a": { phase: "pull" as const, started_at: "x", run_id: "y" } };
    const base = { data: { "h1:a": { ...UPDATE } }, hosts: HOST_CHECKED };
    expect(summarizeImages({ ...base, applying }, false)).toContain("1 mit Update · 1 Update wird eingespielt");
    expect(summarizeImages({ ...base, applying: { ...applying, "h1:b": applying["h1:a"] } }, false)).toContain("2 Updates werden eingespielt");
    expect(summarizeImages({ data: {}, hosts: {}, applying }, false)).toBe("Image-Updates: 1 Update wird eingespielt");
    expect(summarizeImages(base, false)).not.toContain("eingespielt");
  });
});

describe("ServiceMatrixPage ohne Container", () => {
  function emptyFetch(tag?: string) {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/ext/service-matrix/widgets/matrix")) return new Response(JSON.stringify({ data: [], meta: {} }), { status: 200 });
      if (url.endsWith("/extensions/service-matrix/settings")) return new Response(JSON.stringify({ values: tag ? { docker_host_tag: tag } : {} }), { status: 200 });
      throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
    }));
  }

  it("sagt, was zu tun ist: Server anlegen und markieren – mit Links, nicht nur mit einer Frage", async () => {
    emptyFetch();
    render(<ServiceMatrixPage />);
    expect(await screen.findByText("Noch kein Server mit Docker")).toBeInTheDocument();
    expect(screen.getByText(/Markierung „docker“/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Server & Zugänge öffnen" })).toHaveAttribute("href", "/settings/hosts");
    expect(screen.getByRole("link", { name: "Moduleinstellungen" })).toHaveAttribute("href", "/settings/extensions/service-matrix");
    expect(document.body.textContent).not.toContain("getaggt");
  });

  it("nennt die eingestellte Markierung; ohne Recht keine Links, aber der Text bleibt", async () => {
    (window.__lattice.hasPermission as ReturnType<typeof vi.fn>).mockReturnValue(false);
    emptyFetch("container");
    render(<ServiceMatrixPage />);
    expect(await screen.findByText(/Markierung „container“/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Server & Zugänge öffnen" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Moduleinstellungen" })).toBeNull();
  });
});


describe("statusText (Docker-Status auf Deutsch)", () => {
  it.each([
    ["Up 2 hours", "Läuft seit 2 Stunden"],
    ["Up 1 day", "Läuft seit 1 Tag"],
    ["Up 3 days", "Läuft seit 3 Tagen"],
    ["Up About an hour", "Läuft seit etwa einer Stunde"],
    ["Up About a minute", "Läuft seit etwa einer Minute"],
    ["Up Less than a second", "Läuft seit weniger als einer Sekunde"],
    ["Up 5 minutes (unhealthy)", "Läuft seit 5 Minuten (nicht gesund)"],
    ["Up 4 seconds (health: starting)", "Läuft seit 4 Sekunden (wird geprüft)"],
    ["Up 2 weeks (Paused)", "Läuft seit 2 Wochen (pausiert)"],
    ["Exited (0) 10 hours ago", "Beendet (Code 0) vor 10 Stunden"],
    ["Exited (137) 3 days ago", "Beendet (Code 137) vor 3 Tagen"],
    ["Restarting (1) 5 seconds ago", "Startet neu (Code 1), zuletzt vor 5 Sekunden"],
    ["Created", "Angelegt, noch nicht gestartet"],
    ["Paused", "Pausiert"],
  ])("%s", (raw, expected) => {
    expect(statusText(raw)).toBe(expected);
  });

  it("was nicht passt, bleibt wie es ist (z. B. die Meldung eines nicht erreichbaren Servers)", () => {
    expect(statusText("Nicht erreichbar: timeout")).toBe("Nicht erreichbar: timeout");
    expect(statusText("Exited (1)")).toBe("Exited (1)");
    expect(statusText("Up gestern")).toBe("Up gestern");
    expect(statusText("")).toBe("");
    expect(statusText(null)).toBe("");
  });
});
