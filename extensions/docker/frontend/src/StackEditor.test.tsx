import { StrictMode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { StackEditor, forServer, savedAt } from "./StackEditor";

const OLD = "services:\n  web:\n    image: busybox:1.37\n";
const SHA = "a".repeat(64);
const FILES = [
  { path: "/opt/stacks/shop/compose.yaml", content: OLD, error: null, sha256: SHA },
  { path: "/opt/stacks/shop/extra.yml", content: null, error: "Die Datei gibt es nicht mehr." },
  { path: "/opt/stacks/shop/override.yaml", content: "services: {}\n", error: null, sha256: "b".repeat(64) },
];
const OK = { ok: true, error: null, warnings: [], services: ["web"] };

const requests: string[] = [];
const bodies: Record<string, unknown>[] = [];

function serve(over: Record<string, (init?: RequestInit) => Response> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url.replace(/^.*\/stacks\/shop/, "")}`);
    if (typeof init?.body === "string") bodies.push(JSON.parse(init.body));
    for (const [part, answer] of Object.entries(over)) {
      if (url.includes(part)) return answer(init);
    }
    if (url.endsWith("/stacks/shop/file")) return Response.json({ project: "shop", working_dir: "/opt/stacks/shop", files: FILES });
    if (url.endsWith("/stacks/shop/check")) return Response.json(OK);
    if (url.endsWith("/stacks/shop/history")) return Response.json({ entries: [] });
    throw new Error(url);
  }));
}

const confirm = () => window.__lattice.confirmDialog as Mock;

function edit(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "Inhalt von compose.yaml" }), { target: { value: text } });
}

beforeEach(() => {
  requests.length = 0;
  bodies.length = 0;
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("Stack-Editor", () => {
  it("prüft den Entwurf, ohne etwas zu speichern", async () => {
    serve();
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    const box = await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    expect((box as HTMLTextAreaElement).value).toBe(OLD);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    edit(OLD.replace("1.37", "1.38"));
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    expect(await screen.findByText("Compose kann die Datei lesen (Dienste: web).")).toBeInTheDocument();
    expect(bodies).toEqual([{ path: "/opt/stacks/shop/compose.yaml", content: OLD.replace("1.37", "1.38") }]);
    expect(requests.some((r) => r.includes("/save"))).toBe(false);
  });

  it("Formular und Text sind dieselbe Datei, der Unterschied steht darunter", async () => {
    serve();
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    expect(screen.queryByTestId("stack-diff")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Formular" }));
    const image = screen.getByRole("textbox", { name: "Image von web" });
    fireEvent.focus(image);
    fireEvent.change(image, { target: { value: "busybox:1.38" } });
    fireEvent.blur(image);
    const diff = screen.getByTestId("stack-diff");
    expect(diff).toHaveAttribute("open");
    expect(diff.textContent).toContain("+1 −1 Zeilen");
    expect(diff.textContent).toContain("+     image: busybox:1.38");
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect((screen.getByRole("textbox", { name: "Inhalt von compose.yaml" }) as HTMLTextAreaElement).value).toBe(OLD.replace("1.37", "1.38"));
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await screen.findByText("Compose kann die Datei lesen (Dienste: web).");
    expect(bodies).toEqual([{ path: "/opt/stacks/shop/compose.yaml", content: OLD.replace("1.37", "1.38") }]);
  });

  it("eine abgelehnte Datei wird gar nicht erst vorgeschlagen", async () => {
    serve({ "/check": () => Response.json({ ok: false, error: "validating compose.yaml: services.web.foo false schema", warnings: [], services: [] }) });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services:\n  web:\n    foo: 1\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Compose lehnt die Datei ab: validating compose.yaml: services.web.foo false schema");
    expect(confirm()).not.toHaveBeenCalled();
    expect(requests.some((r) => r.includes("/save"))).toBe(false);
  });

  it("speichert über die Freigabe und nennt riskante Einstellungen vorher", async () => {
    const onClose = vi.fn();
    serve({
      "/check": () => Response.json({ ...OK, warnings: ["„web“ bekommt alle Rechte des Servers (privileged)."] }),
      "/save": () => Response.json({ action_id: "a1", status: "proposed", risk: "high", detail: null }, { status: 202 }),
      "/actions/a1/approve": () => Response.json({
        id: "a1", status: "succeeded", result: { output: "„compose.yaml“ von Stack „shop“ gespeichert.", detail: { saved: true } },
      }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit(OLD + "    privileged: true\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern und anwenden" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("„compose.yaml“ von Stack „shop“ gespeichert."));
    const question = String(confirm().mock.calls[0][0]);
    expect(question).toMatch(/^„compose.yaml“ von Stack „shop“ speichern und den Stack danach anwenden\? /);
    expect(question).toContain("Geänderte Dienste werden dabei neu erstellt und sind kurz nicht erreichbar.");
    expect(question).toContain("Riskant: „web“ bekommt alle Rechte des Servers (privileged). Kommt so eine Einstellung neu dazu, braucht das Speichern eine Freigabe mit hohem Risiko.");
    expect(bodies.at(-1)).toEqual({ path: "/opt/stacks/shop/compose.yaml", content: OLD + "    privileged: true\n", base_sha256: SHA, apply: true });
  });

  it("ohne eigene Freigabe-Rechte wartet der Vorschlag", async () => {
    (window.__lattice.hasPermission as Mock).mockReturnValue(false);
    const onClose = vi.fn();
    serve({ "/save": () => Response.json({ action_id: "a2", status: "proposed", risk: "medium", detail: null }, { status: 202 }) });
    render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("Zum Speichern vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“."));
    expect(requests.some((r) => r.includes("/approve"))).toBe(false);
  });

  it("gespeichert, aber das Anwenden scheitert: der Editor schließt mit beidem", async () => {
    const onClose = vi.fn();
    serve({
      "/save": () => Response.json({ action_id: "a3", status: "proposed", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a3/approve": () => Response.json({
        id: "a3", status: "failed",
        result: { output: "„compose.yaml“ gespeichert.", error: "Gespeichert, aber das Anwenden ist gescheitert: port is already allocated", detail: { saved: true } },
      }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern und anwenden" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onClose.mock.calls[0][0]).toBe(
      "„compose.yaml“ gespeichert.\nFehlgeschlagen: Gespeichert, aber das Anwenden ist gescheitert: port is already allocated",
    );
  });

  it("ist die Datei inzwischen anders, steht der Grund da und sie lässt sich neu laden", async () => {
    serve({
      "/save": () => Response.json({ detail: "Die Datei wurde inzwischen auf dem Server geändert. Lade sie neu und trage deine Änderung noch einmal ein." }, { status: 409 }),
    });
    const onClose = vi.fn();
    render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Die Datei wurde inzwischen auf dem Server geändert.");
    expect(onClose).not.toHaveBeenCalled();
    confirm().mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Datei neu laden" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalledTimes(2));
    expect((screen.getByRole("textbox", { name: "Inhalt von compose.yaml" }) as HTMLTextAreaElement).value).toBe("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Datei neu laden" }));
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Inhalt von compose.yaml" }) as HTMLTextAreaElement).value).toBe(OLD));
  });

  it("alte Fassungen aus dem Verlauf in den Editor laden", async () => {
    const entry = { id: "20261016T002410123456Z-91341d2f", path: "/opt/stacks/shop/compose.yaml", saved_at: "2026-10-16T00:24:10+00:00", size: 42 };
    serve({
      "/history/": () => Response.json({ ...entry, content: "services:\n  web:\n    image: busybox:1.36\n" }),
      "/history": () => Response.json({ entries: [entry] }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    fireEvent.click(screen.getByRole("button", { name: "Verlauf" }));
    const list = await screen.findByTestId("stack-editor-history");
    expect(list.textContent).toContain(`${savedAt(entry.saved_at)} · compose.yaml · 42 Bytes`);
    fireEvent.click(within(list).getByRole("button", { name: "In den Editor laden" }));
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Inhalt von compose.yaml" }) as HTMLTextAreaElement).value).toContain("1.36"));
    expect(screen.getByText(/steht jetzt im Editor\. Gespeichert ist sie erst nach „Speichern“\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
  });

  it("ein leerer Verlauf sagt, wie er sich füllt", async () => {
    serve();
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    fireEvent.click(screen.getByRole("button", { name: "Verlauf" }));
    expect((await screen.findByTestId("stack-editor-history")).textContent).toBe("Noch keine alte Fassung: Der Verlauf füllt sich mit jedem Speichern.");
  });

  it("mehrere Dateien: nur lesbare zum Umschalten, mit Rückfrage bei offener Änderung", async () => {
    serve();
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    expect(screen.queryByRole("button", { name: "extra.yml" })).toBeNull();
    edit("geändert\n");
    confirm().mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "override.yaml" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalledWith("Deine Änderung ist noch nicht gespeichert. Verwerfen?", { confirmLabel: "Verwerfen" }));
    expect(screen.getByRole("textbox", { name: "Inhalt von compose.yaml" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "override.yaml" }));
    expect((await screen.findByRole("textbox", { name: "Inhalt von override.yaml" }) as HTMLTextAreaElement).value).toBe("services: {}\n");
  });

  it("Schließen ohne Änderung fragt nicht", async () => {
    const onClose = vi.fn();
    serve();
    render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith(null));
    expect(confirm()).not.toHaveBeenCalled();
  });

  it("nicht geprüft ist keine Ablehnung", async () => {
    serve({ "/check": () => Response.json({ ok: false, error: null, unverified: "Server nicht erreichbar: Zeitüberschreitung", warnings: [], services: [] }) });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Nicht geprüft: Server nicht erreichbar: Zeitüberschreitung");
    expect(screen.queryByText(/Compose lehnt/)).toBeNull();
    expect(requests.some((r) => r.includes("/save"))).toBe(false);
  });

  it("lief die Aktion gleich durch, holt der Editor das Ergebnis nach", async () => {
    const onClose = vi.fn();
    serve({
      "/save": () => Response.json({ action_id: "a4", status: "succeeded", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a4": () => Response.json({ id: "a4", status: "succeeded", result: { output: "„compose.yaml“ gespeichert.", detail: { saved: true } } }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("„compose.yaml“ gespeichert."));
  });

  it("eine sofort gescheiterte Aktion nennt den echten Grund", async () => {
    serve({
      "/save": () => Response.json({ action_id: "a5", status: "failed", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a5": () => Response.json({ id: "a5", status: "failed", result: { error: "Die Datei ließ sich nicht schreiben: kein Platz" } }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Fehlgeschlagen: Die Datei ließ sich nicht schreiben: kein Platz");
  });

  it("Windows-Zeilenenden bleiben erhalten", async () => {
    const crlf = OLD.replace(/\n/g, "\r\n");
    serve({ "/file": () => Response.json({ project: "shop", working_dir: "/opt/stacks/shop", files: [{ ...FILES[0], content: crlf }] }) });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    const box = await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    expect((box as HTMLTextAreaElement).value).toBe(OLD);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    edit(OLD.replace("1.37", "1.38"));
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await screen.findByText("Compose kann die Datei lesen (Dienste: web).");
    expect(bodies.at(-1)).toEqual({ path: "/opt/stacks/shop/compose.yaml", content: crlf.replace("1.37", "1.38") });
    expect(forServer("a\nb\n", "x\r\n")).toBe("a\r\nb\r\n");
    expect(forServer("a\nb\n", "x\n")).toBe("a\nb\n");
  });

  it("ein spätes Prüfergebnis gilt nicht für inzwischen getippten Text", async () => {
    let answer: (r: Response) => void = () => {};
    serve({ "/check": () => new Promise<Response>((resolve) => { answer = resolve; }) as unknown as Response });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await waitFor(() => expect(requests.some((r) => r.includes("/check"))).toBe(true));
    edit("services: {kaputt\n");
    answer(Response.json(OK));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText(/Compose kann die Datei lesen/)).toBeNull();
  });

  it("nach einer Änderung auf dem Server lässt sich die eigene Fassung behalten", async () => {
    let calls = 0;
    serve({
      "/file": () => {
        calls += 1;
        const content = calls === 1 ? OLD : "services:\n  web:\n    image: busybox:1.39\n";
        return Response.json({ project: "shop", working_dir: "/opt/stacks/shop", files: [{ ...FILES[0], content, sha256: calls === 1 ? SHA : "c".repeat(64) }] });
      },
      "/save": () => Response.json({ detail: "Die Datei wurde inzwischen auf dem Server geändert." }, { status: 409 }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("meine Fassung\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Stand vom Server holen, meine Fassung behalten" }));
    await waitFor(() => expect(calls).toBe(2));
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Inhalt von compose.yaml" }) as HTMLTextAreaElement).value).toBe("meine Fassung\n"));
    expect(screen.getByText("Die Datei auf dem Server hat sich geändert. Dein Speichern ersetzt diese neue Fassung; sie steht danach im Verlauf.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await screen.findByText(/Compose kann die Datei lesen/);
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(bodies.some((b) => (b as { base_sha256?: string }).base_sha256 === "c".repeat(64))).toBe(true));
  });

  it("Verweise und andere nicht bearbeitbare Dateien nennen den Grund", async () => {
    serve({
      "/file": () => Response.json({ project: "shop", working_dir: "/opt/stacks/shop", files: [
        { ...FILES[0], edit_error: "Die Datei ist ein Verweis auf eine andere Datei. Bearbeite sie dort, wo sie wirklich liegt." },
      ] }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    expect(await screen.findByText(/Diese Datei lässt sich hier nicht bearbeiten: Die Datei ist ein Verweis/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("läuft auch unter StrictMode bis zum Ende", async () => {
    const onClose = vi.fn();
    serve({
      "/save": () => Response.json({ action_id: "a6", status: "proposed", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a6/approve": () => Response.json({ id: "a6", status: "succeeded", result: { output: "gespeichert", detail: { saved: true } } }),
    });
    render(<StrictMode><StackEditor hostId="h1" project="shop" onClose={onClose} /></StrictMode>);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("gespeichert"));
  });

  it("nach dem Schließen meldet sich eine laufende Speicherung nicht mehr", async () => {
    const onClose = vi.fn();
    let approve: (r: Response) => void = () => {};
    serve({
      "/save": () => Response.json({ action_id: "a7", status: "proposed", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a7/approve": () => new Promise<Response>((resolve) => { approve = resolve; }) as unknown as Response,
    });
    const view = render(<StackEditor hostId="h1" project="shop" onClose={onClose} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(requests.some((r) => r.includes("/approve"))).toBe(true));
    view.unmount();
    approve(Response.json({ id: "a7", status: "executing" }));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("lässt sich das Ergebnis nicht abrufen, sagt der Editor, wo es steht", async () => {
    serve({
      "/save": () => Response.json({ action_id: "a8", status: "succeeded", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a8": () => Response.json({ detail: "Server weg" }, { status: 502 }),
    });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Das Ergebnis ließ sich nicht abrufen \(.*\)\. Ob gespeichert wurde, steht unter „Aktionen“\.$/);
  });

  it("reißt die Verbindung bei der Freigabe ab, gilt der Vorschlag als abgeschickt", async () => {
    serve({
      "/save": () => Response.json({ action_id: "a9", status: "proposed", risk: "medium", detail: null }, { status: 202 }),
      "/actions/a9/approve": () => { throw new TypeError("Failed to fetch"); },
    });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Inhalt von compose.yaml" });
    edit("services: {}\n");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Das Ergebnis ließ sich nicht abrufen \(.*\)\. Ob gespeichert wurde, steht unter „Aktionen“\.$/);
  });

  it("ein Ladefehler ist kein leerer Editor", async () => {
    serve({ "/file": () => Response.json({ detail: "Server nicht erreichbar" }, { status: 502 }) });
    render(<StackEditor hostId="h1" project="shop" onClose={vi.fn()} />);
    expect((await screen.findByTestId("load-error")).textContent).toContain("Server nicht erreichbar");
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
