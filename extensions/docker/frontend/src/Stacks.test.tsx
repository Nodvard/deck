import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ContainersPage } from "./ContainersPage";
import { changesText, membersText, type StackOut } from "./Stacks";

const base = (over: Partial<StackOut>): StackOut => ({
  project: "x", state: "running", running: 1, problems: 0, total: 1, containers: [], source: "compose", working_dir: "/opt/stacks/x",
  config_files: ["/opt/stacks/x/compose.yaml"], is_self: false, changes: { changed: [], added: [], removed: [] }, changed: false,
  check_error: null, adopted: false, adoption_note: null, ...over,
});

const STACKS: StackOut[] = [
  base({ project: "media", working_dir: "/opt/stacks/media" }),
  base({
    project: "shop", running: 2, total: 2, working_dir: "/opt/stacks/shop", changed: true,
    changes: { changed: ["web"], added: ["cache"], removed: [] },
    containers: [
      { name: "shop-web-1", service: "web", state: "running", status: "Up 6 minutes" },
      { name: "shop-worker-1", service: "worker", state: "running", status: "Up 6 minutes (unhealthy)" },
    ],
  }),
  base({ project: "teil", state: "partial", running: 1, problems: 1, total: 3 }),
  base({ project: "weg", changes: null, changed: null, check_error: 'Die Datei ließ sich nicht prüfen: compose file "/opt/stacks/weg/docker-compose.yml" is invalid' }),
  base({ project: "viele", changes: null, changed: null, check_error: "Nicht geprüft: Je Server werden höchstens 60 Stacks geprüft." }),
  base({ project: "app", adopted: true, running: 2, total: 2 }),
  base({ project: "leer", adopted: true, state: "stopped", running: 0, total: 0, changed: null, changes: null }),
  base({ project: "umgezogen", adoption_note: "Übernommen aus /opt/alt, der Stack läuft inzwischen aber mit anderen Dateien. Übernimm ihn neu, damit Nodvard Deck die richtigen kennt." }),
  base({ project: "portainer", source: "portainer", working_dir: "/data/compose/3", config_files: [], changes: null, changed: null, state: "running", running: 1, total: 1 }),
];

const requests: string[] = [];
const bodies: unknown[] = [];

function serve(over: Record<string, (init?: RequestInit) => Response> = {}) {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    urls.push(url);
    requests.push(`${init?.method ?? "GET"} ${url}`);
    if (typeof init?.body === "string") bodies.push(JSON.parse(init.body));
    for (const [part, answer] of Object.entries(over)) {
      if (url.includes(part)) return answer(init);
    }
    if (url.endsWith("/ext/docker/hosts")) return Response.json({ hosts: [{ id: "h1", name: "pi-host" }] });
    if (url.endsWith("/stacks")) return Response.json({ host_id: "h1", stacks: STACKS, check_error: null });
    if (url.endsWith("/stacks/usage")) return Response.json({ host_id: "h1", usage: { shop: { cpu_percent: 1.75, mem_used: 2 * 1024 ** 2 }, media: { cpu_percent: 0.1, mem_used: null } } });
    if (url.endsWith("/stacks/shop/file")) {
      return Response.json({ project: "shop", working_dir: "/opt/stacks/shop", files: [
        { path: "/opt/stacks/shop/compose.yaml", content: "services:\n  web:\n    image: busybox:1.37\n", error: null },
        { path: "/opt/stacks/shop/extra.yml", content: null, error: "Die Datei gibt es nicht mehr." },
      ], env: [
        { path: "/opt/stacks/shop/.env", names: ["DB_PASSWORD", "TZ"], error: null },
        { path: "/opt/stacks/shop/fehlt.env", names: [], error: "Die Datei gibt es nicht mehr." },
        { path: "/opt/stacks/shop/prod.env", names: ["A"], error: null, note: "Ab Zeile 2 liest Nodvard Deck die Namen nicht mehr sicher." },
      ] });
    }
    if (url.includes("/widgets/matrix")) return Response.json({ data: [], meta: {} });
    if (url.includes("/image-updates")) return Response.json({ data: {}, hosts: {} });
    if (url.includes("/ext/docker/stats")) return Response.json({ data: {} });
    throw new Error(url);
  }));
  return urls;
}

beforeEach(() => {
  requests.length = 0;
  bodies.length = 0;
  window.history.replaceState({}, "", "/ext/docker/matrix?tab=stacks");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("Reiter Stacks", () => {
  it("zeigt Zustand, Verbrauch und was noch nicht angewendet ist", async () => {
    serve();
    render(<ContainersPage />);
    const shop = await screen.findByTestId("stack-shop");
    expect(within(shop).getByText("Datei geändert, noch nicht angewendet")).toBeInTheDocument();
    expect(shop.textContent).toContain("geändert: web · neu in der Datei: cache. Übernommen wird das erst");
    expect(shop.textContent).toContain("Container: shop-web-1, shop-worker-1 (ungesund)");
    await waitFor(() => expect(shop.textContent).toContain("2 von 2 Containern laufen · /opt/stacks/shop · CPU 1.8 % · RAM 2.0 MB"));
    expect(screen.getByTestId("stack-media").textContent).toContain("CPU 0.1 % · RAM n.\u00a0v.");
    expect(screen.getByTestId("stack-media").textContent).not.toContain("geändert");
    expect(within(screen.getByTestId("stack-teil")).getByText("läuft teilweise")).toBeInTheDocument();
    expect(within(screen.getByTestId("stack-teil")).getByText("1 Container mit Problemen")).toBeInTheDocument();
    expect(screen.getByTestId("stack-weg").textContent).toContain("Die Datei ließ sich nicht prüfen: compose file");
    // Nicht geprüft ist nicht dasselbe wie „nicht geändert“.
    expect(screen.getByTestId("stack-viele").textContent).toContain("Nicht geprüft: Je Server werden höchstens 60 Stacks geprüft.");
    const portainer = screen.getByTestId("stack-portainer");
    expect(portainer.textContent).toContain("Von Portainer verwaltet");
    expect(within(portainer).queryByRole("button", { name: "Datei ansehen" })).toBeNull();
  });

  it("die Datei zum Ansehen, eine fehlende mit Grund", async () => {
    serve();
    render(<ContainersPage />);
    const shop = await screen.findByTestId("stack-shop");
    fireEvent.click(within(shop).getByRole("button", { name: "Datei ansehen" }));
    const file = await screen.findByTestId("stack-file-shop");
    expect(file.querySelector("pre")?.textContent).toBe("services:\n  web:\n    image: busybox:1.37\n");
    expect(file.textContent).toContain("/opt/stacks/shop/extra.yml");
    expect(file.textContent).toContain("Die Datei gibt es nicht mehr.");
    const [env, missing, partly] = within(file).getAllByTestId("stack-env");
    expect(partly.textContent).toBe("/opt/stacks/shop/prod.envVariablen: AAb Zeile 2 liest Nodvard Deck die Namen nicht mehr sicher.");
    expect(env.textContent).toBe("/opt/stacks/shop/.envVariablen: DB_PASSWORD, TZ");
    expect(missing.textContent).toBe("/opt/stacks/shop/fehlt.envDie Datei gibt es nicht mehr.");
    expect(file.textContent).toContain("Die Werte aus .env zeigt Nodvard Deck nicht");
  });

  it("ein Ladefehler ist keine leere Liste, und eine geplatzte Prüfung wird genannt", async () => {
    serve({
      "/stacks/usage": () => Response.json({ usage: {} }),
      "/stacks": () => new Response(JSON.stringify({ detail: "Cannot connect to the Docker daemon" }), { status: 502 }),
    });
    render(<ContainersPage />);
    expect((await screen.findByTestId("load-error")).textContent).toContain("Cannot connect to the Docker daemon");
    expect(screen.queryByText("Auf diesem Server läuft kein Compose-Projekt.")).toBeNull();
  });

  it("nennt, wenn die Prüfung auf Änderungen nicht lief", async () => {
    serve({
      "/stacks/usage": () => Response.json({ usage: {} }),
      "/stacks": () => Response.json({ stacks: [], check_error: "Server nicht erreichbar: timeout" }),
    });
    render(<ContainersPage />);
    expect(await screen.findByText(/ließ sich nicht prüfen: Server nicht erreichbar: timeout/)).toBeInTheDocument();
    expect(screen.getByText("Auf diesem Server läuft kein Compose-Projekt.")).toBeInTheDocument();
  });

  it("ohne hosts.execute gibt es den Reiter nicht", async () => {
    (window.__nodvardDeck.hasPermission as ReturnType<typeof vi.fn>).mockReturnValue(false);
    const urls = serve();
    render(<ContainersPage />);
    await screen.findByText("Noch kein Server mit Docker");
    expect(screen.queryByRole("button", { name: "Stacks" })).toBeNull();
    expect(urls.some((u) => u.includes("/stacks"))).toBe(false);
  });
});

describe("Übernehmen", () => {
  const adoptUrl = (project: string) => `/stacks/${project}/adopt`;

  it("erst prüfen, dann übernehmen; am Ende steht es da", async () => {
    const urls = serve({
      [adoptUrl("media")]: (init) => (init?.method === "POST"
        ? Response.json({ project: "media", adopted: true })
        : Response.json({ ok: true, reasons: [], working_dir: "/opt/stacks/media", config_files: ["/opt/stacks/media/compose.yaml"], env_files: [] })),
    });
    render(<ContainersPage />);
    const media = await screen.findByTestId("stack-media");
    fireEvent.click(within(media).getByRole("button", { name: "Übernehmen" }));
    const panel = await screen.findByTestId("adopt-media");
    expect(panel.textContent).toContain("Am Server ändert sich dabei nichts");
    expect(panel.textContent).toContain("Gemerkt werden der Ordner /opt/stacks/media und /opt/stacks/media/compose.yaml.");
    fireEvent.click(within(panel).getByRole("button", { name: "Jetzt übernehmen" }));
    expect(await screen.findByText("„media“ ist übernommen.")).toBeInTheDocument();
    expect(requests).toContain(`POST /api/v1/ext/docker/hosts/h1${adoptUrl("media")}`);
    expect(bodies[0]).toEqual({ working_dir: "/opt/stacks/media", config_files: ["/opt/stacks/media/compose.yaml"], env_files: [] });
    expect(urls.filter((u) => u.endsWith("/stacks")).length).toBe(2);
  });

  it("mit Grund dagegen gibt es keinen Knopf", async () => {
    serve({ [adoptUrl("media")]: () => Response.json({ ok: false, reasons: ["Das ist Nodvard Deck selbst."], working_dir: "/opt/x", config_files: [], env_files: [] }) });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-media")).getByRole("button", { name: "Übernehmen" }));
    const panel = await screen.findByTestId("adopt-media");
    expect(panel.textContent).toContain("So lässt sich der Stack nicht übernehmen:Das ist Nodvard Deck selbst.");
    expect(within(panel).queryByRole("button", { name: "Jetzt übernehmen" })).toBeNull();
  });

  it("übernommen, auch ohne Container; loslassen nur nach Rückfrage", async () => {
    serve({ [adoptUrl("leer")]: () => Response.json({ project: "leer", adopted: false }) });
    render(<ContainersPage />);
    const leer = await screen.findByTestId("stack-leer");
    expect(within(leer).getByText("übernommen")).toBeInTheDocument();
    expect(leer.textContent).toContain("gerade keine Container");
    // Ohne Container gibt es nichts, was eine Änderung anwenden könnte.
    expect(leer.textContent).not.toContain("geändert");
    expect(leer.textContent).not.toContain("Nicht geprüft");
    fireEvent.click(within(leer).getByRole("button", { name: "Übernahme aufheben" }));
    await waitFor(() => expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalled());
    const question = String((window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0]);
    expect(question).toContain("Container bleiben, wie sie sind");
    expect(question).toContain("verschwindet er danach aus der Liste");
    expect(requests.some((r) => r.startsWith("DELETE"))).toBe(false);

    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    fireEvent.click(within(leer).getByRole("button", { name: "Übernahme aufheben" }));
    expect(await screen.findByText("Übernahme von „leer“ aufgehoben.")).toBeInTheDocument();
    expect(requests).toContain(`DELETE /api/v1/ext/docker/hosts/h1${adoptUrl("leer")}`);
  });

  it("nach einem Umzug: Hinweis und „Neu übernehmen“", async () => {
    serve();
    render(<ContainersPage />);
    const moved = await screen.findByTestId("stack-umgezogen");
    expect(moved.textContent).toContain("Übernimm ihn neu");
    expect(within(moved).getByRole("button", { name: "Neu übernehmen" })).toBeInTheDocument();
    expect(within(moved).getByRole("button", { name: "Übernahme aufheben" })).toBeInTheDocument();
    expect(within(moved).queryByText("übernommen")).toBeNull();
  });

  it("scheitert das Übernehmen, steht der Grund beim Knopf, die Prüfung bleibt stehen", async () => {
    serve({
      [adoptUrl("media")]: (init) => (init?.method === "POST"
        ? new Response(JSON.stringify({ detail: "Der Stack läuft inzwischen mit anderen Dateien als bei der Prüfung." }), { status: 409 })
        : Response.json({ ok: true, reasons: [], working_dir: "/opt/stacks/media", config_files: ["/opt/stacks/media/compose.yaml"], env_files: ["/opt/stacks/media/prod.env"] })),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-media")).getByRole("button", { name: "Übernehmen" }));
    const panel = await screen.findByTestId("adopt-media");
    expect(panel.textContent).toContain("/opt/stacks/media/compose.yaml, /opt/stacks/media/prod.env");
    fireEvent.click(within(panel).getByRole("button", { name: "Jetzt übernehmen" }));
    expect((await within(panel).findByRole("alert")).textContent).toBe(
      "Übernehmen hat nicht geklappt: Der Stack läuft inzwischen mit anderen Dateien als bei der Prüfung.",
    );
    expect(within(panel).getByRole("button", { name: "Jetzt übernehmen" })).toBeInTheDocument();
    const checks = requests.filter((r) => r.startsWith("GET") && r.endsWith(adoptUrl("media"))).length;
    fireEvent.click(within(panel).getByRole("button", { name: "Prüfung neu laden" }));
    await waitFor(() => expect(requests.filter((r) => r.startsWith("GET") && r.endsWith(adoptUrl("media"))).length).toBe(checks + 1));
  });
});

describe("Stack-Aktionen", () => {
  it("nur für übernommene Stacks, und nur, was gerade passt", async () => {
    serve();
    render(<ContainersPage />);
    const app = await screen.findByTestId("stack-app");
    expect(within(app).getAllByRole("button").map((b) => b.textContent)).toEqual(
      ["Datei ansehen", "Bearbeiten", "Aus Git", "Anwenden", "Stoppen", "Herunterfahren", "Images holen", "Übernahme aufheben", "Update", "Sicherungen",
        "Löschen"],
    );
    expect(within(screen.getByTestId("stack-media")).queryByRole("button", { name: "Bearbeiten" })).toBeNull();
    expect(within(screen.getByTestId("stack-media")).queryByRole("button", { name: "Aus Git" })).toBeNull();
    const leer = screen.getByTestId("stack-leer");
    expect(within(leer).queryByRole("button", { name: "Stoppen: leer" })).toBeNull();
    expect(within(leer).queryByRole("button", { name: "Herunterfahren: leer" })).toBeNull();
    expect(within(leer).getByRole("button", { name: "Anwenden: leer" })).toBeInTheDocument();
    expect(within(leer).queryByRole("button", { name: "Update" })).toBeNull();
    expect(within(screen.getByTestId("stack-media")).queryByRole("button", { name: "Anwenden: media" })).toBeNull();
  });

  it("über die Freigabe, mit Rückfrage und Ergebnis", async () => {
    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    serve({
      "/actions/docker.stack_down": () => Response.json({ id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "/actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { output: "Stack „app“ heruntergefahren." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Herunterfahren: app" }));
    expect(await screen.findByText("Stack „app“ heruntergefahren.")).toBeInTheDocument();
    const question = String((window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0]);
    expect(question).toMatch(/^Stack „app“ herunterfahren\? /);
    expect(question).toContain("Was nur im Container lag und nicht in einem Volume oder Ordner, ist danach weg.");
    expect(requests).toContain("POST /api/v1/hosts/h1/actions/docker.stack_down");
    expect(bodies[0]).toEqual({ payload: { project: "app" }, reason: "Stack „app“ über die Docker-Seite: Herunterfahren." });
  });

  it("Löschen: der Ordner nur angehakt, mit Warnung, über die Freigabe", async () => {
    const confirm = window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>;
    confirm.mockResolvedValue(true);
    serve({
      "/stacks/app/delete": (init) => init?.method === "POST"
        ? Response.json({ action_id: "d1", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json({ folder: "/opt/stacks/app", folder_refusal: null }),
      "/actions/d1/approve": () => Response.json({ id: "d1", status: "succeeded", result: {
        output: "Stack „app“ heruntergefahren und gelöscht. Der Ordner /opt/stacks/app ist gelöscht. Die Volumes bleiben; unbenutzte löschst du im Reiter Volumes." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Löschen" }));
    const panel = await screen.findByTestId("delete-app");
    expect(panel.textContent).toContain("Die Volumes bleiben.");
    expect(within(panel).queryByText(/ein Zurück gibt es nicht/)).toBeNull();
    fireEvent.click(within(panel).getByRole("checkbox"));
    expect(within(panel).getByText(/Sie sind danach weg, ein Zurück gibt es nicht/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Stack löschen" }));
    expect(await screen.findByText(/Der Ordner \/opt\/stacks\/app ist gelöscht\./)).toBeInTheDocument();
    expect(String(confirm.mock.calls[0][0])).toBe(
      "Stack „app“ löschen? Auch der Ordner /opt/stacks/app mit allen Dateien darin wird gelöscht; ein Zurück gibt es nicht. Die Volumes bleiben.");
    expect(bodies.at(-1)).toEqual({ remove_folder: true });
    expect(screen.queryByTestId("delete-app")).toBeNull();
  });

  it("Löschen: ein Ordner, der bleiben muss, lässt sich nicht anhaken", async () => {
    serve({ "/stacks/app/delete": () => Response.json({ folder: "/opt/stacks/app", folder_refusal: "Den Ordner /opt/stacks/app löscht Nodvard Deck nicht: Darin liegt noch ein anderer Stack." }) });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Löschen" }));
    const panel = await screen.findByTestId("delete-app");
    expect(within(panel).getByRole("checkbox")).toBeDisabled();
    expect(panel.textContent).toContain("Darin liegt noch ein anderer Stack.");
  });

  it("Löschen: ein Fehler ohne Ergebnis bleibt im Bereich stehen", async () => {
    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    serve({
      "/stacks/app/delete": (init) => init?.method === "POST"
        ? Response.json({ action_id: "d2", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json({ folder: "/opt/stacks/app", folder_refusal: null }),
      "/actions/d2/approve": () => Response.json({ id: "d2", status: "failed", result: { error: "Der Stack „app“ ist nicht übernommen." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Löschen" }));
    const panel = await screen.findByTestId("delete-app");
    fireEvent.click(within(panel).getByRole("button", { name: "Stack löschen" }));
    expect((await within(panel).findByRole("alert")).textContent).toContain("Der Stack „app“ ist nicht übernommen.");
  });

  it("Löschen: ein Teilerfolg steht als Warnung über der Liste", async () => {
    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    serve({
      "/stacks/app/delete": (init) => init?.method === "POST"
        ? Response.json({ action_id: "d3", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json({ folder: "/opt/stacks/app", folder_refusal: null }),
      "/actions/d3/approve": () => Response.json({ id: "d3", status: "failed", result: {
        output: "Stack „app“ heruntergefahren.", error: "Der Ordner /opt/stacks/app ließ sich nicht ganz löschen: Permission denied." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Löschen" }));
    fireEvent.click(within(await screen.findByTestId("delete-app")).getByRole("button", { name: "Stack löschen" }));
    const warning = await screen.findByRole("alert");
    expect(warning.textContent).toContain("Stack „app“ heruntergefahren.");
    expect(warning.textContent).toContain("ließ sich nicht ganz löschen");
    expect(screen.queryByTestId("delete-app")).toBeNull();
  });

  it("Update: wählt die Dienste mit Update, nennt Datenspeicher und bietet den Rückweg an", async () => {
    const confirm = window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>;
    confirm.mockResolvedValue(true);
    const plan = {
      services: [
        { service: "db", container: "app-db-1", image: "postgres:16", status: "update", reason: null, data_store: true, backup: false },
        { service: "web", container: "app-web-1", image: "nginx:1.27", status: "update", reason: null, data_store: false, backup: true },
        { service: "worker", container: "app-worker-1", image: "eigenes:1", status: "local", reason: null, data_store: false, backup: false },
      ],
      updates: ["db", "web"], data_store: true, rollback: ["web"],
    };
    serve({
      "/stacks/app/update": (init) => init?.method === "POST"
        ? Response.json({ action_id: "u1", status: "proposed", risk: "medium", detail: null }, { status: 202 })
        : Response.json(plan),
      "/actions/u1/approve": () => Response.json({ id: "u1", status: "succeeded", result: { output: "Stack „app“ aktualisiert: web. Prüfung danach: läuft stabil. Die alten Images bleiben als Sicherung." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    const panel = await screen.findByTestId("update-app");
    expect(panel.textContent).toContain("selbst gebaut, keine Registry");
    expect(panel.textContent).toContain("Dabei ist ein Datenspeicher (db)");
    fireEvent.click(within(panel).getByRole("checkbox", { name: "db aktualisieren" }));
    expect(panel.textContent).not.toContain("Dabei ist ein Datenspeicher");
    expect(within(panel).getByRole("button", { name: "Zurück zur vorigen Version (web)" })).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Jetzt aktualisieren" }));
    expect(await screen.findByText(/Stack „app“ aktualisiert: web\./)).toBeInTheDocument();
    expect(String(confirm.mock.calls[0][0])).toMatch(/^Stack „app“ aktualisieren \(web\)\? /);
    expect(bodies.at(-1)).toEqual({ services: ["web"], backup: false });
  });

  it("Update: Volumes vorher sichern, mit Größe, Platz und Grund, wenn es nicht geht", async () => {
    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    const plan = {
      services: [
        { service: "web", container: "app-web-1", image: "nginx:1.27", status: "update", reason: null, data_store: false, backup: false, volumes: ["app_daten"] },
        { service: "cache", container: "app-cache-1", image: "redis:7", status: "update", reason: null, data_store: true, backup: false, volumes: [] },
      ],
      updates: ["web", "cache"], data_store: true, rollback: [],
      volume_backup: { volumes: { app_daten: 2 * 1024 ** 3 }, free_bytes: 50 * 1024 ** 3, folder: "/home/deck/nodvard-sicherungen/app/", error: null },
    };
    serve({
      "/stacks/app/update": (init) => init?.method === "POST"
        ? Response.json({ action_id: "u2", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json(plan),
      "/actions/u2/approve": () => Response.json({ id: "u2", status: "succeeded", result: { output: "Stack „app“ aktualisiert: web, cache. Volumes gesichert in /home/deck/nodvard-sicherungen/app/x." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    const panel = await screen.findByTestId("update-app");
    const tick = within(panel).getByRole("checkbox", { name: /Volumes vorher sichern \(app_daten: 2(\.0|,0)? GB, frei 50(\.0|,0)? GB\)/ });
    expect(panel.textContent).toContain("Die Archive kommen nach /home/deck/nodvard-sicherungen/app/");
    fireEvent.click(tick);
    fireEvent.click(within(panel).getByRole("button", { name: "Jetzt aktualisieren" }));
    expect(await screen.findByText(/Volumes gesichert in/)).toBeInTheDocument();
    expect(bodies.at(-1)).toEqual({ services: ["web", "cache"], backup: true });
  });

  it("Update: ohne Platz lässt sich die Sicherung nicht anhaken", async () => {
    serve({ "/stacks/app/update": () => Response.json({
      services: [{ service: "web", container: "app-web-1", image: "nginx:1.27", status: "update", reason: null, data_store: false, backup: false, volumes: ["app_daten"] }],
      updates: ["web"], data_store: false, rollback: [],
      volume_backup: { volumes: { app_daten: 9 * 1024 ** 3 }, free_bytes: 1024 ** 3, folder: "/home/deck/nodvard-sicherungen/app/", error: "Zu wenig Platz: Die Volumes brauchen bis zu 9,0 GB, frei sind 1,0 GB." },
    }) });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    const panel = await screen.findByTestId("update-app");
    expect(within(panel).getByRole("checkbox", { name: /Volumes vorher sichern/ })).toBeDisabled();
    expect(panel.textContent).toContain("Zu wenig Platz");
  });

  it("Update: der Rückweg geht über die eigene Adresse", async () => {
    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    serve({
      "/stacks/app/rollback": () => Response.json({ action_id: "r1", status: "proposed", risk: "high", detail: null }, { status: 202 }),
      "/stacks/app/update": () => Response.json({ services: [], updates: [], data_store: false, rollback: ["web"] }),
    });
    window.__nodvardDeck.hasPermission = vi.fn((permission: string) => !permission.startsWith("actions.approve:"));
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    const panel = await screen.findByTestId("update-app");
    expect(panel.textContent).toContain("Für keinen Dienst meldet die Image-Prüfung gerade ein Update.");
    fireEvent.click(within(panel).getByRole("button", { name: "Zurück zur vorigen Version (web)" }));
    expect(await screen.findByText("Vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.")).toBeInTheDocument();
    expect(requests).toContain("POST /api/v1/ext/docker/hosts/h1/stacks/app/rollback");
  });

  it("Update: nicht angewendete Änderungen sperren, gleiche Images kommen mit, gestoppte bleiben", async () => {
    const confirm = window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>;
    confirm.mockResolvedValue(true);
    let unapplied = ["web"];
    const plan = () => ({
      services: [
        { service: "web", container: "app-web-1", image: "nginx:1.27", status: "update", reason: null, data_store: false, backup: true, backup_at: "2026-10-14T08:00:00+00:00" },
        { service: "web2", container: "app-web2-1", image: "nginx:1.27", status: "current", reason: null, data_store: false, backup: false },
        { service: "cron", container: "app-cron-1", image: "busybox:1.37", status: "update", reason: null, data_store: false, backup: false, stopped: true },
      ],
      updates: ["web"], data_store: false, rollback: ["web"], unapplied, unapplied_error: null,
    });
    serve({
      "/stacks/app/update": (init) => init?.method === "POST"
        ? Response.json({ action_id: "u2", status: "proposed", risk: "medium", detail: null }, { status: 202 })
        : Response.json(plan()),
      "/actions/u2/approve": () => Response.json({ id: "u2", status: "failed", result: { error: "Update von web, web2 nicht sauber gelaufen. Zurück auf der vorigen Version, sie läuft wieder." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    let panel = await screen.findByTestId("update-app");
    expect(within(panel).getByRole("alert").textContent).toContain("In der Datei steht für web etwas anderes, als gerade läuft.");
    expect(within(panel).getByRole("button", { name: "Jetzt aktualisieren" })).toBeDisabled();
    expect(within(panel).getByRole("button", { name: /^Zurück zur vorigen Version \(web, Sicherung vom 14\.10\.2026\)$/ })).toBeDisabled();
    expect(within(panel).queryByRole("checkbox", { name: "cron aktualisieren" })).toBeNull();
    expect(panel.textContent).toContain("Gestoppte Dienste aktualisiert Deck hier nicht");
    expect(panel.textContent).toContain("web2 nutzt dasselbe Image und kommt mit.");
    unapplied = [];
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Update" }));
    panel = await screen.findByTestId("update-app");
    await waitFor(() => expect(within(panel).getByRole("button", { name: "Jetzt aktualisieren" })).toBeEnabled());
    fireEvent.click(within(panel).getByRole("button", { name: "Jetzt aktualisieren" }));
    const warning = await screen.findByRole("alert");
    expect(warning.textContent).toContain("nicht sauber gelaufen");
    expect(screen.queryByTestId("update-app")).toBeNull();
    expect(String(confirm.mock.calls[0][0])).toMatch(/^Stack „app“ aktualisieren \(web, web2\)\? /);
    expect(bodies.at(-1)).toEqual({ services: ["web"], backup: false });
  });

  it("Umzug von Portainer: Ordner, Warnung und Freigabe", async () => {
    const confirm = window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>;
    confirm.mockResolvedValue(true);
    serve({
      "/stacks/portainer/from-portainer": (init) => init?.method === "POST"
        ? Response.json({ action_id: "p1", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json({ project: "portainer", folder: "/opt/stacks/portainer", portainer_id: "3", services: ["app"], problem: null }),
      "/actions/p1/approve": () => Response.json({ id: "p1", status: "succeeded", result: {
        output: "Stack „portainer“ liegt jetzt in /opt/stacks/portainer und ist übernommen. Prüfung danach: Container läuft stabil." } }),
    });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-portainer")).getByRole("button", { name: "Von Portainer übernehmen" }));
    const panel = await screen.findByTestId("portainer-portainer");
    expect(panel.textContent).toContain("/opt/stacks/portainer");
    expect(panel.textContent).toContain("Lösche den Stack danach nicht in Portainer");
    expect(panel.textContent).toContain("was nur in den alten Containern lag und nicht in einem Volume oder Ordner, geht dabei verloren.");
    fireEvent.click(within(panel).getByRole("button", { name: "Umziehen" }));
    expect(await screen.findByText(/liegt jetzt in \/opt\/stacks\/portainer/)).toBeInTheDocument();
    expect(String(confirm.mock.calls[0][0])).toMatch(/^Stack „portainer“ von Portainer nach \/opt\/stacks\/portainer holen/);
    expect(String(confirm.mock.calls[0][0])).toContain("was nur in den alten Containern lag und nicht in einem Volume oder Ordner, ist danach weg.");
    expect(requests).toContain("POST /api/v1/ext/docker/hosts/h1/stacks/portainer/from-portainer");
  });

  it("Umzug von Portainer: ein Grund sperrt den Knopf", async () => {
    serve({ "/stacks/portainer/from-portainer": () => Response.json({
      project: "portainer", folder: "/opt/stacks/portainer", portainer_id: "3", services: ["app"],
      problem: "„app“ bindet ./daten ein. Die Daten liegen auf dem Server unter /data/compose/3/daten.",
    }) });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-portainer")).getByRole("button", { name: "Von Portainer übernehmen" }));
    const panel = await screen.findByTestId("portainer-portainer");
    expect(within(panel).getByRole("alert").textContent).toContain("/data/compose/3/daten");
    expect(within(panel).getByRole("button", { name: "Umziehen" })).toBeDisabled();
  });

  it("Nodvard Deck selbst bekommt keinen Umzug", async () => {
    const own = base({ project: "deck", source: "portainer", working_dir: "/data/compose/9", config_files: [], changes: null, changed: null, is_self: true });
    STACKS.push(own);
    try {
      serve();
      render(<ContainersPage />);
      const row = await screen.findByTestId("stack-deck");
      expect(within(row).queryByRole("button", { name: "Von Portainer übernehmen" })).toBeNull();
      expect(within(await screen.findByTestId("stack-portainer")).getByRole("button", { name: "Von Portainer übernehmen" })).toBeInTheDocument();
    } finally {
      STACKS.pop();
    }
  });

  it("Umzug von Portainer: ein gestoppter Stack wird vorher genannt", async () => {
    const confirm = window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>;
    confirm.mockResolvedValue(false);
    serve({ "/stacks/portainer/from-portainer": () => Response.json({
      project: "portainer", folder: "/opt/stacks/portainer", portainer_id: "3", services: ["app"], stopped: true, problem: null,
    }) });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-portainer")).getByRole("button", { name: "Von Portainer übernehmen" }));
    const panel = await screen.findByTestId("portainer-portainer");
    expect(panel.textContent).toContain("Der Stack ist gerade gestoppt; der Umzug startet ihn.");
    fireEvent.click(within(panel).getByRole("button", { name: "Umziehen" }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(String(confirm.mock.calls[0][0])).toContain("Der Stack ist gerade gestoppt; der Umzug startet ihn.");
    expect(requests.some((r) => r.startsWith("POST"))).toBe(false);
  });

  it("eine Freigabe, die noch aussteht, nennt den Stack", async () => {
    (window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mockResolvedValue(true);
    (window.__nodvardDeck.hasPermission as ReturnType<typeof vi.fn>).mockImplementation((p: string) => !p.startsWith("actions.approve"));
    serve({ "/actions/docker.stack_pull": () => Response.json({ id: "a2", status: "proposed", risk: "medium" }, { status: 202 }) });
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Images holen: app" }));
    expect(await screen.findByText(/^Stack „app“, Images holen: vorgeschlagen/)).toBeInTheDocument();
    expect(String((window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>).mock.calls[0][0])).toMatch(/^Images für den Stack „app“ neu holen\? /);
  });

  it("nach „Abbrechen“ geht nichts raus", async () => {
    serve();
    render(<ContainersPage />);
    fireEvent.click(within(await screen.findByTestId("stack-app")).getByRole("button", { name: "Stoppen: app" }));
    await waitFor(() => expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalled());
    expect(requests.some((r) => r.startsWith("POST"))).toBe(false);
  });
});

describe("Bearbeiten", () => {
  it("öffnet den Editor nur bei übernommenen Stacks und lädt nach dem Speichern die Liste neu", async () => {
    serve({
      "/stacks/app/file": () => Response.json({ project: "app", working_dir: "/opt/stacks/x", files: [
        { path: "/opt/stacks/x/compose.yaml", content: "services: {}\n", error: null, sha256: "a".repeat(64) },
      ] }),
    });
    render(<ContainersPage />);
    const app = await screen.findByTestId("stack-app");
    fireEvent.click(within(app).getByRole("button", { name: "Bearbeiten" }));
    expect(await screen.findByTestId("stack-editor-app")).toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("stack-editor-app")).getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(screen.queryByTestId("stack-editor-app")).toBeNull());
  });

  it("ein anderer Knopf schließt den Editor mit offener Änderung nur nach Rückfrage", async () => {
    serve({
      "/stacks/app/file": () => Response.json({ project: "app", working_dir: "/opt/stacks/x", files: [
        { path: "/opt/stacks/x/compose.yaml", content: "services: {}\n", error: null, sha256: "a".repeat(64) },
      ] }),
    });
    const confirm = window.__nodvardDeck.confirmDialog as ReturnType<typeof vi.fn>;
    confirm.mockResolvedValue(false);
    render(<ContainersPage />);
    const app = await screen.findByTestId("stack-app");
    fireEvent.click(within(app).getByRole("button", { name: "Bearbeiten" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" }), { target: { value: "geändert\n" } });
    fireEvent.click(within(app).getByRole("button", { name: "Datei ansehen" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("Deine Änderung im Editor ist noch nicht gespeichert. Verwerfen?", { confirmLabel: "Verwerfen" }));
    expect(screen.getByTestId("stack-editor-app")).toBeInTheDocument();
    confirm.mockResolvedValue(true);
    fireEvent.click(within(app).getByRole("button", { name: "Datei ansehen" }));
    await waitFor(() => expect(screen.queryByTestId("stack-editor-app")).toBeNull());
  });
});

describe("Neuer Stack", () => {
  it("öffnet das Formular über der Liste", async () => {
    serve({ "/new-stack": () => Response.json({ root: "/opt/stacks", default: "/opt/stacks", root_state: "dir", root_problem: null, setting_problem: null, home: "/home/deck" }) });
    render(<ContainersPage />);
    await screen.findByTestId("stacks");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Stack" }));
    expect(await screen.findByTestId("new-stack")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Neuer Stack" })).toBeNull();
  });
});

describe("changesText", () => {
  it("nennt nur, was aussteht", () => {
    expect(changesText({ changed: [], added: [], removed: ["alt", "rest"] })).toBe("nicht mehr in der Datei: alt, rest");
    expect(changesText({ changed: [], added: [], removed: [] })).toBe("");
  });

  it("Container mit ihrem Zustand, wenn er vom Laufen abweicht", () => {
    expect(membersText([
      { name: "a", service: "a", state: "running", status: "Up" },
      { name: "b", service: "b", state: "exited", status: "Exited (0)" },
      { name: "c", service: "c", state: "restarting", status: "Restarting (1)" },
    ])).toBe("a, b (gestoppt), c (startet neu)");
  });
});
