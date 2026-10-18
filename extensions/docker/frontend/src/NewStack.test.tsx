import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { EXAMPLE, NewStack } from "./NewStack";

const PLACE = { root: "/opt/stacks", default: "/opt/stacks", root_state: "dir", root_problem: null, setting_problem: null, home: "/home/deck" };
const OK = { ok: true, error: null, unverified: null, warnings: [], services: ["app"], folder: "/opt/stacks/neu", conflict: null };

const TEMPLATES = [
  { id: "uptime-kuma", name: "Uptime Kuma", summary: "Überwacht Webseiten.", compose: "services:\n  uptime-kuma:\n    image: louislam/uptime-kuma:2.5.6\n",
    note: "Danach unter Port 3001 öffnen.", ports: ["3001"] },
  { id: "gitea", name: "Gitea", summary: "Eigener Git-Server.", compose: "services:\n  gitea:\n    image: gitea/gitea:28.1.0\n",
    note: "Weboberfläche unter Port 3000.", ports: ["3000", "2222"] },
];

const requests: string[] = [];
const bodies: Record<string, unknown>[] = [];

function serve(over: Record<string, (init?: RequestInit) => Response> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url.replace(/^.*\/hosts\/h1/, "")}`);
    if (typeof init?.body === "string") bodies.push(JSON.parse(init.body));
    for (const [part, answer] of Object.entries(over)) {
      if (url.includes(part)) return answer(init);
    }
    if (url.endsWith("/stack-templates")) return Response.json({ templates: TEMPLATES });
    if (url.endsWith("/new-stack/check")) return Response.json(OK);
    if (url.endsWith("/new-stack")) return Response.json(PLACE);
    throw new Error(url);
  }));
}

const confirm = () => window.__lattice.confirmDialog as Mock;
const nameBox = () => screen.getByRole("textbox", { name: "Name des Stacks" });

beforeEach(() => {
  requests.length = 0;
  bodies.length = 0;
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: vi.fn().mockReturnValue(true),
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("Neuer Stack", () => {
  it("zeigt Ordner und Beispiel, prüft den Namen schon beim Tippen", async () => {
    serve();
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    expect((await screen.findByTestId("new-stack")).textContent).toContain("Ordner für neue Stacks: /opt/stacks");
    expect((screen.getByRole("textbox", { name: "Compose-Datei des neuen Stacks" }) as HTMLTextAreaElement).value).toBe(EXAMPLE);
    expect(screen.getByRole("button", { name: "Anlegen" })).toBeDisabled();
    fireEvent.change(nameBox(), { target: { value: "Mein Stack" } });
    expect(screen.getByText(/Nur Kleinbuchstaben, Ziffern/)).toBeInTheDocument();
    fireEvent.change(nameBox(), { target: { value: "vaultwarden" } });
    expect(screen.getByText("/opt/stacks/vaultwarden")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anlegen" })).toBeEnabled();
  });

  it("eine Vorlage füllt Name und Datei und nennt Hinweis und Ports", async () => {
    serve();
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    const box = await screen.findByRole("combobox", { name: "Vorlage" });
    fireEvent.change(box, { target: { value: "uptime-kuma" } });
    await waitFor(() => expect((nameBox() as HTMLInputElement).value).toBe("uptime-kuma"));
    const text = () => (screen.getByRole("textbox", { name: "Compose-Datei des neuen Stacks" }) as HTMLTextAreaElement).value;
    expect(text()).toBe(TEMPLATES[0].compose);
    expect(screen.getByTestId("template-note").textContent).toBe("Überwacht Webseiten.Danach unter Port 3001 öffnen.Belegt auf dem Server den Port 3001.");
    expect(confirm()).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "gitea" } });
    await waitFor(() => expect(text()).toBe(TEMPLATES[1].compose));
    expect((nameBox() as HTMLInputElement).value).toBe("gitea");
    expect(screen.getByTestId("template-note").textContent).toContain("die Ports 3000, 2222");
    fireEvent.change(nameBox(), { target: { value: "git" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Compose-Datei des neuen Stacks" }), { target: { value: "services: {}\n" } });
    confirm().mockResolvedValueOnce(false);
    fireEvent.change(box, { target: { value: "uptime-kuma" } });
    await waitFor(() => expect(confirm()).toHaveBeenCalledWith("Den bisherigen Text durch die Vorlage ersetzen?", { confirmLabel: "Ersetzen" }));
    expect(text()).toBe("services: {}\n");
    confirm().mockResolvedValueOnce(true);
    fireEvent.change(box, { target: { value: "uptime-kuma" } });
    await waitFor(() => expect(text()).toBe(TEMPLATES[0].compose));
    // Einen selbst gewählten Namen ersetzt die Vorlage nicht.
    expect((nameBox() as HTMLInputElement).value).toBe("git");
  });

  it("Beispiel, Datei und eigene Änderungen lassen keinen falschen Hinweis stehen", async () => {
    serve();
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    const box = await screen.findByRole("combobox", { name: "Vorlage" });
    const area = () => screen.getByRole("textbox", { name: "Compose-Datei des neuen Stacks" }) as HTMLTextAreaElement;
    fireEvent.change(area(), { target: { value: "" } });
    fireEvent.change(box, { target: { value: "uptime-kuma" } });
    await waitFor(() => expect(area().value).toBe(TEMPLATES[0].compose));
    expect(confirm()).not.toHaveBeenCalled();
    fireEvent.change(area(), { target: { value: TEMPLATES[0].compose.replace("2.5.6", "2.5.7") } });
    expect(screen.getByTestId("template-note").textContent).toContain("Du hast die Datei geändert");
    fireEvent.click(screen.getByRole("button", { name: "Beispiel einfügen" }));
    expect(screen.queryByTestId("template-note")).toBeNull();
    expect(area().value).toBe(EXAMPLE);
    fireEvent.change(box, { target: { value: "gitea" } });
    await waitFor(() => expect(screen.getByTestId("template-note")).toBeInTheDocument());
    const file = new File(["services:\n  eigen:\n    image: busybox:1.37\n"], "compose.yaml", { type: "text/yaml" });
    fireEvent.change(screen.getByLabelText("Compose-Datei hochladen"), { target: { files: [file] } });
    await waitFor(() => expect(area().value).toContain("eigen:"));
    expect(screen.queryByTestId("template-note")).toBeNull();
  });

  it("nach einer Vorlage geht genau ihr Text ans Anlegen", async () => {
    serve({
      "/new-stack/check": () => Response.json(OK),
      "/new-stack": (init) => init?.method === "POST"
        ? Response.json({ action_id: "c1", status: "proposed", risk: "medium", detail: null }, { status: 202 }) : Response.json(PLACE),
    });
    window.__lattice.hasPermission = vi.fn().mockReturnValue(false);
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Vorlage" }), { target: { value: "gitea" } });
    await waitFor(() => expect((nameBox() as HTMLInputElement).value).toBe("gitea"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Anlegen" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(bodies.at(-1)).toEqual({ name: "gitea", content: TEMPLATES[1].compose, start: false }));
  });

  it("ohne Vorlagen bleibt alles bedienbar und der Grund steht da", async () => {
    serve({ "/stack-templates": () => new Response("kaputt", { status: 500 }) });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    expect(await screen.findByText(/Die Vorlagen/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Vorlage" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Compose-Datei des neuen Stacks" })).toBeInTheDocument();
  });

  it("legt über die Freigabe an und nennt riskante Einstellungen vorher", async () => {
    const onClose = vi.fn();
    serve({
      "/new-stack/check": () => Response.json({ ...OK, warnings: ["„app“ bekommt alle Rechte des Servers (privileged)."] }),
      "/new-stack": (init) => init?.method === "POST"
        ? Response.json({ action_id: "c1", status: "proposed", risk: "high", detail: null }, { status: 202 })
        : Response.json(PLACE),
      "/actions/c1/approve": () => Response.json({ id: "c1", status: "succeeded", result: { output: "Stack „neu“ in /opt/stacks/neu angelegt und übernommen.", detail: { created: true } } }),
    });
    render(<NewStack hostId="h1" onClose={onClose} />);
    await screen.findByTestId("new-stack");
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen und starten" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("Stack „neu“ in /opt/stacks/neu angelegt und übernommen."));
    const question = String(confirm().mock.calls[0][0]);
    expect(question).toMatch(/^Stack „neu“ in \/opt\/stacks\/neu anlegen und starten\? /);
    expect(question).toContain("Riskant: „app“ bekommt alle Rechte des Servers (privileged). Das Anlegen braucht dafür eine Freigabe mit hohem Risiko.");
    expect(bodies.at(-1)).toEqual({ name: "neu", content: EXAMPLE, start: true });
  });

  it.each([
    ["wartet auf die Freigabe", { action_id: "c1", status: "proposed", risk: "medium", detail: null }, null,
      "Zum Anlegen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“."],
    ["angelegt, aber der Start scheitert", { action_id: "c1", status: "proposed", risk: "medium", detail: null },
      { id: "c1", status: "failed", result: { output: "Stack „neu“ in /opt/stacks/neu angelegt und übernommen.", error: "Angelegt, aber das Starten ist gescheitert: pull access denied", detail: { created: true } } },
      "Stack „neu“ in /opt/stacks/neu angelegt und übernommen.\n"],
  ])("Ergebnis: %s", async (_name, proposed, approved, closed) => {
    const onClose = vi.fn();
    confirm().mockResolvedValue(true);
    serve({
      "/new-stack/check": () => Response.json(OK),
      "/new-stack": (init) => init?.method === "POST" ? Response.json(proposed, { status: 202 }) : Response.json(PLACE),
      ...(approved ? { "/actions/c1/approve": () => Response.json(approved) } : {}),
    });
    if (!approved) window.__lattice.hasPermission = vi.fn().mockReturnValue(false);
    render(<NewStack hostId="h1" onClose={onClose} />);
    await screen.findByTestId("new-stack");
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    fireEvent.click(screen.getByRole("button", { name: approved ? "Anlegen und starten" : "Anlegen" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(String(onClose.mock.calls[0][0])).toContain(closed);
  });

  it("gescheitert ohne Ordner: der Grund bleibt im Formular", async () => {
    const onClose = vi.fn();
    serve({
      "/new-stack/check": () => Response.json(OK),
      "/new-stack": (init) => init?.method === "POST"
        ? Response.json({ action_id: "c1", status: "proposed", risk: "medium", detail: null }, { status: 202 })
        : Response.json(PLACE),
      "/actions/c1/approve": () => Response.json({ id: "c1", status: "failed", result: { error: "Den Ordner /opt/stacks/neu gibt es inzwischen.", detail: null } }),
    });
    render(<NewStack hostId="h1" onClose={onClose} />);
    await screen.findByTestId("new-stack");
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Den Ordner /opt/stacks/neu gibt es inzwischen.");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Netzfehler nach dem Absenden: kein falsches Ergebnis", async () => {
    serve({
      "/new-stack/check": () => Response.json(OK),
      "/new-stack": (init) => init?.method === "POST"
        ? Response.json({ action_id: "c1", status: "proposed", risk: "medium", detail: null }, { status: 202 })
        : Response.json(PLACE),
      "/actions/c1/approve": () => { throw new TypeError("Failed to fetch"); },
    });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/^Das Ergebnis ließ sich nicht abrufen \(.+\)\. Ob angelegt wurde, steht unter „Aktionen“\.$/);
  });

  it("nennt einen untauglichen Ordner und eine ungültige Einstellung", async () => {
    serve({ "/new-stack": () => Response.json({ ...PLACE, root_state: "link", root_problem: "/opt/stacks ist ein Verweis auf einen anderen Ordner.",
      setting_problem: "Der eingestellte Ordner /tmp/x in den Einstellungen des Moduls taugt nicht, deshalb gilt /opt/stacks." }) });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    const panel = await screen.findByTestId("new-stack");
    expect(panel.textContent).toContain("/opt/stacks ist ein Verweis auf einen anderen Ordner.");
    expect(panel.textContent).toContain("Der eingestellte Ordner /tmp/x in den Einstellungen des Moduls taugt nicht");
    expect(panel.textContent).not.toContain("gibt es noch nicht");
  });

  it("das Beispiel lässt sich im Formular anpassen", async () => {
    serve();
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    fireEvent.click(screen.getByRole("button", { name: "Formular" }));
    const image = screen.getByRole("textbox", { name: "Image von app" });
    fireEvent.focus(image);
    fireEvent.change(image, { target: { value: "nginx:1.28" } });
    fireEvent.blur(image);
    fireEvent.click(screen.getByRole("button", { name: "Prüfen" }));
    await screen.findByText(/Compose kann die Datei lesen/);
    expect(bodies).toEqual([{ name: "neu", content: EXAMPLE.replace("nginx:1.27", "nginx:1.28") }]);
  });

  it("ein belegter Name oder Ordner verhindert das Anlegen", async () => {
    serve({ "/new-stack/check": () => Response.json({ ...OK, conflict: "Den Ordner /opt/stacks/neu gibt es schon." }) });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Den Ordner /opt/stacks/neu gibt es schon.");
    expect(confirm()).not.toHaveBeenCalled();
    expect(requests.some((r) => r === "POST /new-stack")).toBe(false);
  });

  it("eine Datei hochladen ersetzt den Text", async () => {
    serve();
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    const file = new File(["services:\r\n  db:\r\n    image: postgres:17\r\n"], "compose.yaml", { type: "text/yaml" });
    fireEvent.change(screen.getByLabelText("Compose-Datei hochladen"), { target: { files: [file] } });
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Compose-Datei des neuen Stacks" }) as HTMLTextAreaElement).value).toBe(
      "services:\n  db:\n    image: postgres:17\n"));
    const big = new File(["x".repeat(256 * 1024 + 1)], "gross.yaml");
    fireEvent.change(screen.getByLabelText("Compose-Datei hochladen"), { target: { files: [big] } });
    expect((await screen.findByRole("alert")).textContent).toBe("Die Datei ist größer als 256 KB.");
  });

  it("der Ordner für neue Stacks lässt sich je Server ändern", async () => {
    let root = "/opt/stacks";
    serve({
      "/stack-root": (init) => {
        root = (JSON.parse(String(init?.body)) as { path: string | null }).path ?? "/opt/stacks";
        return Response.json({ root });
      },
      "/new-stack": () => Response.json({ ...PLACE, root, root_state: root === "/opt/stacks" ? "dir" : "missing" }),
    });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    fireEvent.click(screen.getByRole("button", { name: "Ändern" }));
    fireEvent.click(screen.getByRole("button", { name: "Im Heimatordner" }));
    expect((screen.getByRole("textbox", { name: "Ordner für neue Stacks" }) as HTMLInputElement).value).toBe("/home/deck/stacks");
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    expect(await screen.findByText(/\/home\/deck\/stacks/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("new-stack").textContent).toContain("(gibt es noch nicht, wird mit angelegt)"));
    expect(bodies.at(-1)).toEqual({ path: "/home/deck/stacks" });
  });

  it("ein abgelehnter Ordner nennt den Grund", async () => {
    serve({ "/stack-root": () => Response.json({ detail: "Bitte einen absoluten Ordner angeben, etwa /opt/stacks oder /home/pi/stacks, aber keinen Systemordner." }, { status: 400 }) });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    await screen.findByTestId("new-stack");
    fireEvent.click(screen.getByRole("button", { name: "Ändern" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Ordner für neue Stacks" }), { target: { value: "/etc" } });
    fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
    expect((await screen.findByRole("alert")).textContent).toContain("keinen Systemordner");
  });

  it("schließen mit Eingaben fragt nach, ohne nicht", async () => {
    const onClose = vi.fn();
    serve();
    render(<NewStack hostId="h1" onClose={onClose} />);
    await screen.findByTestId("new-stack");
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith(null));
    expect(confirm()).not.toHaveBeenCalled();
    onClose.mockClear();
    fireEvent.change(nameBox(), { target: { value: "neu" } });
    confirm().mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    await waitFor(() => expect(confirm()).toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ein Ladefehler ist kein leeres Formular", async () => {
    serve({ "/new-stack": () => Response.json({ detail: "Kein Docker-Host." }, { status: 404 }) });
    render(<NewStack hostId="h1" onClose={vi.fn()} />);
    expect((await screen.findByTestId("load-error")).textContent).toContain("Kein Docker-Host.");
  });
});
