import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { GitSourcePanel, type Preview } from "./GitSource";

const SOURCE = { url: "https://github.com/team/stacks", ref: "main", path: "shop/compose.yaml", access: null, set_at: "2026-10-24T00:00:00+00:00" };
const PREVIEW: Preview = {
  commit: { id: "a".repeat(40), short: "aaaaaaa", ref_kind: "branch", author: "Ada Lovelace", date: "2026-10-23T22:00:00+02:00", message: "Neustart" },
  ok: true,
  problems: [],
  warnings: ["„web“ bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: /srv."],
  files: [
    { path: "app.ini", server_path: "/opt/stacks/shop/app.ini", state: "new", size: 4, mode: "644" },
    { path: "compose.yaml", server_path: "/opt/stacks/shop/compose.yaml", state: "changed", size: 60, mode: "644" },
    { path: ".env", server_path: "/opt/stacks/shop/.env", state: "changed", size: 8, mode: "644", env: true },
    { path: "prod.env", server_path: "/opt/stacks/shop/prod.env", state: "changed", size: 8, mode: "644", env: true },
  ],
  compose: { path: "/opt/stacks/shop/compose.yaml", old: "services:\n  web:\n    image: nginx\n", new: "services:\n  web:\n    image: nginx\n    restart: always\n", same: false },
  unchanged: false,
};
const requests: { method: string; url: string; body: unknown }[] = [];

const NOTHING_APPLIED = { applied: null, previous: null };

function serve(state: Record<string, unknown>, over: Record<string, (init?: RequestInit) => Response> = {}, applied: object = NOTHING_APPLIED) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    requests.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const key = `${method} ${url.replace(/^.*\/stacks\/shop/, "").replace(/^.*\/actions\//, "/actions/")}`;
    if (over[key]) return over[key](init);
    if (key === "GET /git") return Response.json(state);
    if (key === "GET /git/applied") return Response.json(applied);
    if (key === "PUT /git") return Response.json({ source: { ...SOURCE, ...(JSON.parse(String(init?.body)) as object) } });
    if (key === "POST /git/fetch") return Response.json(PREVIEW);
    if (key === "DELETE /git") return Response.json({ source: null });
    throw new Error(key);
  }));
}

beforeEach(() => {
  requests.length = 0;
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("Stack aus Git", () => {
  it("trägt eine Quelle ein und zeigt danach den geholten Stand", async () => {
    serve({ source: null, accesses: [{ name: "github", host: "github.com" }], layout_problem: null });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText("Adresse des Repositorys"), { target: { value: " https://github.com/team/stacks " } });
    fireEvent.change(screen.getByLabelText("Compose-Datei im Repository"), { target: { value: "shop/compose.yaml" } });
    fireEvent.change(screen.getByLabelText("Zugang"), { target: { value: "github" } });
    fireEvent.click(screen.getByRole("button", { name: "Eintragen und holen" }));
    const preview = await screen.findByTestId("git-preview");
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({ url: "https://github.com/team/stacks", ref: "main", path: "shop/compose.yaml", access: "github" });
    expect(requests.map((r) => r.method)).toEqual(["GET", "GET", "PUT", "POST"]);
    expect(preview.textContent).toContain("Stand aaaaaaa (Branch main) von Ada Lovelace");
    expect(preview.textContent).toContain("Neustart");
    expect(preview.textContent).toContain("Darauf solltest du achten:");
    expect(preview.textContent).toContain("neu/opt/stacks/shop/app.ini");
    expect(screen.getByTestId("stack-diff").textContent).toContain("+     restart: always");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("holt einen eingetragenen Stand erneut und nennt Gründe dagegen", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/fetch": () => Response.json({ ...PREVIEW, ok: false, problems: ["„link“ ist ein Verweis (Symlink); Verweise holt Deck nicht."] }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByLabelText("Adresse des Repositorys")).toHaveValue(SOURCE.url);
    expect(screen.getByRole("button", { name: "Speichern und holen" })).toBeDisabled();
    expect(screen.getByTestId("git-shop").textContent).toContain("Einstellungen → Erweiterungen → Docker, unter „Erweitert“ bei „Git-Zugänge“");
    fireEvent.click(screen.getByRole("button", { name: "Holen und ansehen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("So ließe sich dieser Stand nicht einspielen:„link“ ist ein Verweis");
    expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
  });

  it("zeigt einen Fehler beim Holen mit Grund", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/fetch": () => Response.json({ detail: "Im Repository gibt es keinen Branch und kein Tag „dev“." }, { status: 502 }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("keinen Branch und kein Tag „dev“");
    expect(screen.queryByTestId("git-preview")).toBeNull();
  });

  it("sagt, wenn auf dem Server schon derselbe Stand steht", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/fetch": () => Response.json({ ...PREVIEW, warnings: [], unchanged: true, compose: { ...PREVIEW.compose!, new: PREVIEW.compose!.old!, same: true },
        files: PREVIEW.files.map((f) => ({ ...f, state: "same" })) }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    expect(await screen.findByText("Auf dem Server steht schon genau dieser Stand.")).toBeInTheDocument();
    expect(screen.queryByTestId("stack-diff")).toBeNull();
    expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
  });

  it("spielt einen angesehenen Stand erst nach der Rückfrage ein", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { success: true, output: "Stand aaaaaaa eingespielt." } }),
    });
    const done = vi.fn();
    render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    (window.__nodvardDeck.confirmDialog as Mock).mockResolvedValueOnce(false);
    fireEvent.click(await screen.findByRole("button", { name: "Einspielen" }));
    await waitFor(() => expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalledTimes(1));
    expect(requests.some((r) => r.url.endsWith("/git/apply"))).toBe(false);
    const question = String((window.__nodvardDeck.confirmDialog as Mock).mock.calls[0][0]);
    expect(question).toContain("schreibt 1 neue und 1 geänderte Dateien");
    expect(question).toContain("Umgebungsdateien wie .env auf dem Server ersetzt es nie. Volumes werden vorher nicht gesichert.");
    expect(screen.getAllByText("bleibt, wie sie ist")).toHaveLength(2);
    expect(question).toContain("Dienste, die geänderte Dateien einlesen, starten neu (die Freigabe nennt sie)");
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("Stand aaaaaaa eingespielt."));
    expect(requests.find((r) => r.url.endsWith("/git/apply"))?.body).toEqual({ commit: "a".repeat(40), backup: false });
  });

  it("sichert auf Wunsch die Volumes vorher und nennt, wer dafür stillsteht", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "GET /git/volume-plan": () => Response.json({ volumes: { shop_data: 2 * 1024 ** 3 }, free_bytes: 50 * 1024 ** 3,
        folder: "/home/deck/nodvard-sicherungen/shop/", error: null, services: ["web"] }),
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { success: true, output: "Eingespielt." } }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Volumes vorher sichern/ }));
    expect(await screen.findByText(/web steht dafür still, bis Compose den Stack anwendet/)).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /shop_data: 2(,|\.)0 GB/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    await waitFor(() => expect(requests.some((r) => r.url.endsWith("/git/apply"))).toBe(true));
    const question = String((window.__nodvardDeck.confirmDialog as Mock).mock.calls[0][0]);
    expect(question).toContain("Vorher sichert es die Volumes; web steht dafür still.");
    expect(question).not.toContain("nicht gesichert");
    expect(requests.find((r) => r.url.endsWith("/git/apply"))?.body).toEqual({ commit: "a".repeat(40), backup: true });
  });

  it("nennt den Grund, wenn sich die Volumes nicht sichern lassen, und spielt dann ohne Sicherung ein", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "GET /git/volume-plan": () => Response.json({ volumes: {}, free_bytes: null, folder: null, services: [],
        error: "Der Stack hat keine benannten Volumes; seine Daten liegen in Ordnern auf dem Server und werden nicht mitgesichert." }),
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { success: true, output: "Eingespielt." } }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    const box = await screen.findByRole("checkbox", { name: /Volumes vorher sichern/ });
    fireEvent.click(box);
    expect(await screen.findByText(/keine benannten Volumes/)).toBeTruthy();
    expect((box as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    await waitFor(() => expect(requests.some((r) => r.url.endsWith("/git/apply"))).toBe(true));
    expect(String((window.__nodvardDeck.confirmDialog as Mock).mock.calls[0][0])).toContain("Volumes werden vorher nicht gesichert.");
    expect(requests.find((r) => r.url.endsWith("/git/apply"))?.body).toEqual({ commit: "a".repeat(40), backup: false });
  });

  it("vergisst einen Plan, der erst nach dem Abwählen ankommt", async () => {
    let answer: (value: Response) => void = () => undefined;
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "GET /git/volume-plan": () => new Promise<Response>((resolve) => { answer = resolve; }) as unknown as Response,
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    const box = await screen.findByRole("checkbox", { name: /Volumes vorher sichern/ });
    fireEvent.click(box);
    expect(await screen.findByText(/Lese die Volumes/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Einspielen" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(box);
    answer(Response.json({ volumes: { shop_data: 1 }, free_bytes: 10, folder: "/home/deck/nodvard-sicherungen/shop/", error: null, services: ["web"] }));
    await waitFor(() => expect(screen.queryByText(/Lese die Volumes/)).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect((box as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByText(/steht dafür still/)).toBeNull();
  });

  it("vergisst auch einen Fehler beim Plan, der erst nach dem Abwählen ankommt", async () => {
    let answer: (value: Response) => void = () => undefined;
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "GET /git/volume-plan": () => new Promise<Response>((resolve) => { answer = resolve; }) as unknown as Response,
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    const box = await screen.findByRole("checkbox", { name: /Volumes vorher sichern/ });
    fireEvent.click(box);
    expect(await screen.findByText(/Lese die Volumes/)).toBeTruthy();
    fireEvent.click(box);
    answer(Response.json({ detail: "Docker auf dem Server ließ sich nicht abfragen." }, { status: 502 }));
    await waitFor(() => expect(screen.queryByText(/Lese die Volumes/)).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(/ließ sich nicht abfragen/)).toBeNull();
  });

  it("nimmt den Haken bei einem neu geholten Stand wieder weg", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "GET /git/volume-plan": () => Response.json({ volumes: { shop_data: 1 }, free_bytes: 10, folder: "/home/deck/nodvard-sicherungen/shop/",
        error: null, services: ["web"] }),
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { success: true, output: "Eingespielt." } }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: /Volumes vorher sichern/ }));
    expect(await screen.findByText(/web steht dafür still/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Holen und ansehen" }));
    await waitFor(() => expect(screen.queryByText(/web steht dafür still/)).toBeNull());
    expect((screen.getByRole("checkbox", { name: /Volumes vorher sichern/ }) as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    await waitFor(() => expect(requests.some((r) => r.url.endsWith("/git/apply"))).toBe(true));
    expect(requests.find((r) => r.url.endsWith("/git/apply"))?.body).toEqual({ commit: "a".repeat(40), backup: false });
  });

  it("sperrt den Haken, solange eingespielt wird", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/apply": () => new Promise<Response>(() => undefined) as unknown as Response,
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    const box = await screen.findByRole("checkbox", { name: /Volumes vorher sichern/ });
    expect((box as HTMLInputElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Einspielen" }));
    expect(await screen.findByRole("button", { name: "Spiele ein …" })).toBeTruthy();
    expect((box as HTMLInputElement).disabled).toBe(true);
  });

  it("wartet ohne Freigabe-Recht auf einen Admin und meldet Fehler beim Einspielen", async () => {
    window.__nodvardDeck.hasPermission = vi.fn().mockReturnValue(false);
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
    });
    const done = vi.fn();
    const { unmount } = render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Einspielen" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(expect.stringContaining("Es fehlt noch die Freigabe")));
    unmount();
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/apply": () => Response.json({ detail: "Im Repository gibt es inzwischen einen anderen Stand." }, { status: 409 }),
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Einspielen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("anderen Stand");
  });

  it("zeigt den eingespielten Stand und holt den Stand davor zurück", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/rollback": () => Response.json({ action_id: "b1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/b1/approve": () => Response.json({ id: "b1", status: "failed",
        result: { success: false, output: "Stack „shop“ steht wieder auf dem Stand vor bbbbbbb.", error: "Geschrieben, aber das Anwenden ist gescheitert: kaputt", detail: { written: true } } }),
    }, {
      applied: { commit: "b".repeat(40), at: "2026-10-24T12:00:00+00:00", files: 2 },
      previous: { id: "c".repeat(32), commit: "b".repeat(40), commit_before: null, at: "2026-10-24T12:00:00+00:00" },
    });
    const done = vi.fn();
    render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    expect((await screen.findByTestId("git-applied")).textContent).toContain("Zuletzt eingespielt: Stand bbbbbbb");
    fireEvent.click(screen.getByRole("button", { name: "Zurück zum Stand davor" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(expect.stringContaining("Anwenden ist gescheitert"), true));
    expect(requests.find((r) => r.url.endsWith("/git/rollback"))?.body).toEqual({ previous: "c".repeat(32) });
    const question = String((window.__nodvardDeck.confirmDialog as Mock).mock.calls[0][0]);
    expect(question).toContain("Was seitdem auf dem Server geändert wurde, bleibt");
    expect(question).toContain("auch eine dabei neu angelegte .env");
    expect(question).toContain("Volumes werden vorher nicht gesichert.");
  });

  it("bietet für Dienste außerhalb der Freigabe einen eigenen Neustart an", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { success: true,
        output: "Stand aaaaaaa eingespielt. Diese Dienste lesen vielleicht noch den alten Stand: web.", detail: { written: true, stale: ["web"] } } }),
      "POST /git/restart": () => Response.json({ action_id: "r1", status: "proposed", risk: "medium" }, { status: 202 }),
      "POST /actions/r1/approve": () => Response.json({ id: "r1", status: "succeeded", result: { success: true, output: "Neu gestartet: web." } }),
    });
    const done = vi.fn();
    render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Einspielen" }));
    expect((await screen.findByTestId("git-stale")).textContent).toContain("lesen vielleicht noch den alten Stand: web");
    expect(done).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Jetzt neu starten" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("Neu gestartet: web."));
    expect(requests.find((r) => r.url.endsWith("/git/restart"))?.body).toEqual({ services: ["web"] });
    expect(String((window.__nodvardDeck.confirmDialog as Mock).mock.calls.at(-1)?.[0])).toContain("neu starten: web?");
  });

  it("lässt den Neustart nach einem Fehler erneut zu und bietet „Später“", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => Response.json({ id: "a1", status: "succeeded", result: { success: true,
        output: "Diese Dienste lesen vielleicht noch den alten Stand: web.", detail: { written: true, stale: ["web"] } } }),
      "POST /git/restart": () => Response.json({ action_id: "r1", status: "proposed", risk: "medium" }, { status: 202 }),
      "POST /actions/r1/approve": () => Response.json({ id: "r1", status: "failed", result: { success: false, error: "Der Neustart von web ist gescheitert: weg" } }),
    });
    const done = vi.fn();
    render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Einspielen" }));
    await screen.findByTestId("git-stale");
    expect(screen.queryByRole("button", { name: "Einspielen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Jetzt neu starten" }));
    expect((await screen.findByRole("alert")).textContent).toContain("gescheitert");
    expect(screen.getByRole("button", { name: "Jetzt neu starten" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Später" }));
    expect(done).toHaveBeenCalledWith("Diese Dienste lesen vielleicht noch den alten Stand: web.", false);
  });

  it("zeigt nach einem Teilstand gleich den Weg zurück", async () => {
    let runs = 0;
    serve({ source: SOURCE, accesses: [], layout_problem: null }, {
      "GET /git/applied": () => Response.json(runs === 0 ? NOTHING_APPLIED : {
        applied: null, previous: { id: "c".repeat(32), commit: "a".repeat(40), commit_before: null, at: "2026-10-24T12:00:00+00:00" },
      }),
      "POST /git/apply": () => Response.json({ action_id: "a1", status: "proposed", risk: "high" }, { status: 202 }),
      "POST /actions/a1/approve": () => {
        runs += 1;
        return Response.json({ id: "a1", status: "failed", result: { success: false,
          error: "Die Dateien ließen sich nicht schreiben: weg. Der alte Stand ließ sich nicht ganz zurückschreiben; „Zurück zum Stand davor“ versucht es noch einmal." } });
      },
    });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Holen und ansehen" }));
    fireEvent.click(await screen.findByRole("button", { name: "Einspielen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("versucht es noch einmal");
    expect(await screen.findByRole("button", { name: "Zurück zum Stand davor" })).toBeTruthy();
  });

  it("vergisst das Repository erst nach der Rückfrage", async () => {
    serve({ source: SOURCE, accesses: [], layout_problem: null });
    const done = vi.fn();
    render(<GitSourcePanel hostId="h1" project="shop" onDone={done} />);
    (window.__nodvardDeck.confirmDialog as Mock).mockResolvedValueOnce(false);
    fireEvent.click(await screen.findByRole("button", { name: "Repository vergessen" }));
    await waitFor(() => expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalledTimes(1));
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Repository vergessen" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith("Das Repository von „shop“ ist vergessen."));
    expect(String((window.__nodvardDeck.confirmDialog as Mock).mock.calls[0][0])).toContain("Stack und Dateien auf dem Server bleiben");
  });

  it("nennt den Grund, wenn der Stack nicht passt, und einen Ladefehler nie als leere Quelle", async () => {
    serve({ source: null, accesses: [], layout_problem: "Der Stack besteht aus mehreren Compose-Dateien." });
    const { unmount } = render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect((await screen.findByTestId("git-shop")).textContent).toBe("Der Stack besteht aus mehreren Compose-Dateien.");
    expect(screen.queryByLabelText("Adresse des Repositorys")).toBeNull();
    unmount();
    serve({ source: SOURCE, accesses: [], layout_problem: "Der Stack besteht aus mehreren Compose-Dateien." });
    const second = render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Repository vergessen" })).toBeInTheDocument();
    second.unmount();
    serve({}, { "GET /git": () => Response.json({ detail: "kaputt" }, { status: 500 }) });
    render(<GitSourcePanel hostId="h1" project="shop" onDone={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Erneut versuchen/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Adresse des Repositorys")).toBeNull();
  });
});
