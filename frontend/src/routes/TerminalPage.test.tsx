import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { TerminalPage, terminalSocketUrl } from "./TerminalPage";

/**
 * xterm.js und WebSocket sind Test-Doubles (jsdom hat weder Canvas noch echte
 * Sockets). Geprueft wird der Vertrag der Seite gegen die bestehende Terminal-API:
 * Ticket holen, WS-Bruecke oeffnen, Bytes in beide Richtungen, Resize als
 * JSON-Steuerframe, Tabs bleiben beim Umschalten verbunden. Die echte Kette (SSH ->
 * WS) ist Backend-seitig bewiesen (test_terminal_api.py) und live im Browser.
 */
class FakeTerminal {
  static instances: FakeTerminal[] = [];
  cols = 120;
  rows = 30;
  written: (string | Uint8Array)[] = [];
  disposed = false;
  private dataHandler: (d: string) => void = () => {};
  private resizeHandler: (s: { cols: number; rows: number }) => void = () => {};
  constructor() {
    FakeTerminal.instances.push(this);
  }
  loadAddon() {}
  open() {}
  focus() {}
  write(data: string | Uint8Array) {
    this.written.push(data);
  }
  onData(handler: (d: string) => void) {
    this.dataHandler = handler;
  }
  onResize(handler: (s: { cols: number; rows: number }) => void) {
    this.resizeHandler = handler;
  }
  dispose() {
    this.disposed = true;
  }
  type(d: string) {
    this.dataHandler(d);
  }
  resize(cols: number, rows: number) {
    this.resizeHandler({ cols, rows });
  }
}

vi.mock("@xterm/xterm", () => ({ Terminal: FakeTerminal }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "blob";
  sent: (string | Uint8Array)[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string | Uint8Array) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
  }
  serverOpen() {
    this.readyState = 1;
    this.onopen?.();
  }
}

const HOSTS = [
  { id: "h-pi", name: "pi-host", display_name: "Raspberry Pi", address: "192.168.1.72", kind: "sbc", status: "up" },
  { id: "h-vm", name: "proxmox-pve2-vm-110", display_name: "win-game", address: "192.168.1.92", kind: "vm", status: "up" },
  { id: "h-none", name: "ohne-ssh", display_name: "ohne-ssh", address: "10.0.0.9", kind: null, status: "up" },
];

function mockFetch(sessions: unknown[], ids: { terminal: string[]; console: string[] } = { terminal: ["h-pi", "h-vm"], console: ["h-vm"] }, hosts: unknown[] = HOSTS) {
  let n = 0;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/v1/hosts")) return new Response(JSON.stringify(hosts));
    if (url.endsWith("/api/v1/terminal/hosts")) return new Response(JSON.stringify(ids.terminal));
    if (url.endsWith("/api/v1/console/hosts")) return new Response(JSON.stringify(ids.console));
    if (url.endsWith("/api/v1/terminal/sessions") && method === "POST") {
      sessions.push(JSON.parse(init?.body as string));
      n += 1;
      return new Response(JSON.stringify({ session_id: `s${n}`, ws_url: `/api/v1/ws/terminal/s${n}` }));
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

function renderPage(path = "/terminal") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TerminalPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  FakeTerminal.instances = [];
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "owner1", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TerminalPage", () => {
  it("ohne Server mit Terminal: verweist auf „Server & Zugänge“ statt auf eine Seite, die es nicht gibt", async () => {
    vi.stubGlobal("fetch", mockFetch([], { terminal: [], console: [] }));
    renderPage();
    const empty = await screen.findByTestId("terminal-empty");
    expect(empty.textContent).toContain("Noch kein Server mit Terminal");
    expect(empty.textContent).toContain("braucht einen SSH-Zugang");
    expect(screen.getByRole("link", { name: "SSH-Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts");
    expect(document.body.textContent).not.toContain("Einstellungen → Hosts");
  });

  it("ohne Recht hosts.write kein Link (die Seite wäre nicht erreichbar), aber derselbe Hinweis", async () => {
    useAuthStore.setState({ user: { id: "u2", username: "viewer", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["hosts.read", "hosts.execute"] } });
    vi.stubGlobal("fetch", mockFetch([], { terminal: [], console: [] }));
    renderPage();
    expect((await screen.findByTestId("terminal-empty")).textContent).toContain("braucht einen SSH-Zugang");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("ganz ohne Server: Knopf „Server hinzufügen“", async () => {
    vi.stubGlobal("fetch", mockFetch([], { terminal: [], console: [] }, []));
    renderPage();
    const empty = await screen.findByTestId("terminal-empty");
    expect(empty.textContent).toContain("Noch kein Server angelegt");
    expect(screen.getByRole("link", { name: "Server hinzufügen" })).toHaveAttribute("href", "/settings/hosts");
  });

  it("Zugänge da, aber kein Terminal: das Modul ist aus – Link zu den Erweiterungen nur mit extensions.manage", async () => {
    const withAccess = HOSTS.map((h) => ({ ...h, credential: { kind: "ssh_key" } }));
    vi.stubGlobal("fetch", mockFetch([], { terminal: [], console: [] }, withAccess));
    renderPage();
    expect((await screen.findByTestId("terminal-empty")).textContent).toContain("Modul „Terminal“ eingeschaltet");
    expect(screen.getByRole("link", { name: "Module ansehen" })).toHaveAttribute("href", "/settings/extensions");
    cleanup();

    useAuthStore.setState({ user: { id: "u2", username: "op", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["hosts.read", "hosts.execute", "hosts.write"] } });
    renderPage();
    await screen.findByTestId("terminal-empty");
    expect(screen.queryByRole("link", { name: "Module ansehen" })).not.toBeInTheDocument();
  });

  it("eine Suche ohne Treffer ist kein Leerzustand der Installation", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByText("Raspberry Pi");
    fireEvent.change(screen.getByLabelText("Server suchen"), { target: { value: "gibtsnicht" } });
    expect(screen.getByText("Kein Server passt zur Suche.")).toBeInTheDocument();
    expect(screen.queryByTestId("terminal-empty")).toBeNull();
  });

  it("bietet nur Hosts mit Terminal an, Konsole zusaetzlich wo verfuegbar", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByText("Raspberry Pi");
    expect(screen.getByText("win-game")).toBeTruthy();
    expect(screen.queryByText("ohne-ssh")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Terminal öffnen" })).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Konsole" }).getAttribute("href")).toBe("/console/h-vm");
  });

  it("oeffnet eine echte Sitzung: Ticket mit Terminalgroesse, Bytes hin und zurueck, Resize als Steuerframe", async () => {
    const sessions: unknown[] = [];
    vi.stubGlobal("fetch", mockFetch(sessions));
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Terminal öffnen" }))[0]);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(sessions).toEqual([{ host_id: "h-pi", cols: 120, rows: 30 }]);
    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toBe(`ws://${window.location.host}/api/v1/ws/terminal/s1`);
    expect(ws.binaryType).toBe("arraybuffer");

    act(() => ws.serverOpen());
    const term = FakeTerminal.instances[0];
    act(() => ws.onmessage?.({ data: new TextEncoder().encode("admin@pi-host:~$ ").buffer }));
    expect(new TextDecoder().decode(term.written[0] as Uint8Array)).toBe("admin@pi-host:~$ ");

    term.type("ls\r");
    expect(new TextDecoder().decode(ws.sent[0] as Uint8Array)).toBe("ls\r");

    term.resize(100, 40);
    expect(JSON.parse(ws.sent[1] as string)).toEqual({ type: "resize", cols: 100, rows: 40 });

    act(() => ws.onmessage?.({ data: JSON.stringify({ type: "exit", code: 0 }) }));
    expect(String(term.written.at(-1))).toContain("Sitzung beendet, Code 0");
    expect(screen.getByRole("button", { name: "Neu verbinden" })).toBeTruthy();
  });

  it("mehrere Tabs: Umschalten laesst die andere Sitzung offen, Schliessen beendet nur die eigene", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    const openButtons = await screen.findAllByRole("button", { name: "Terminal öffnen" });
    fireEvent.click(openButtons[0]);
    fireEvent.click(openButtons[0]);
    fireEvent.click(openButtons[1]);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(3));

    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Raspberry Pi", "Raspberry Pi (2)", "win-game"]);
    expect(screen.getAllByRole("tab")[2].getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getAllByRole("tab")[0]);
    expect(FakeWebSocket.instances.every((ws) => !ws.closed)).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Raspberry Pi (2) schließen" }));
    expect(FakeWebSocket.instances[1].closed).toBe(true);
    expect(FakeWebSocket.instances[0].closed).toBe(false);
    expect(FakeWebSocket.instances[2].closed).toBe(false);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Raspberry Pi", "win-game"]);
  });

  it("Oeffnungsfehler vom Server wird im Klartext gezeigt", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Terminal öffnen" }))[0]);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];
    act(() => ws.serverOpen());
    act(() => ws.onmessage?.({ data: JSON.stringify({ type: "error", message: "Host key mismatch" }) }));
    expect(await screen.findByText("Host key mismatch")).toBeTruthy();
  });

  it("Oeffnungsfehler mit leerem Grund zeigt trotzdem einen Satz statt nichts", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Terminal öffnen" }))[0]);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];
    act(() => ws.serverOpen());
    act(() => ws.onmessage?.({ data: JSON.stringify({ type: "error", message: "" }) }));
    expect(await screen.findByText("Sitzung konnte nicht geöffnet werden.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Neu verbinden" })).toBeTruthy();
  });

  it("Oeffnungsfehler: der deutsche Grund vom Server steht da (Server antwortet nicht)", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Terminal öffnen" }))[0]);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];
    act(() => ws.serverOpen());
    const reason = "Server antwortet nicht (Zeitüberschreitung bei 192.168.2.10:22). Ist er eingeschaltet und im Netz?";
    act(() => ws.onmessage?.({ data: JSON.stringify({ type: "error", message: reason }) }));
    expect(await screen.findByText(reason)).toBeTruthy();
  });

  it("zeigt bei einem Ende durch Leerlauf oder entzogene Anmeldung den Grund statt nur „getrennt“", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Terminal öffnen" }))[0]);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];
    act(() => ws.serverOpen());
    act(() => ws.onclose?.({ code: 4408 }));
    expect(await screen.findByText("Die Sitzung wurde wegen Leerlauf beendet.")).toBeTruthy();
  });

  it("Handy: Hostliste über dem Terminal statt 240 px daneben", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    renderPage();
    await screen.findByText("Raspberry Pi");
    const aside = screen.getByRole("complementary");
    expect(aside).toHaveClass("w-full", "lg:w-60");
    expect(aside).not.toHaveClass("w-60");
    expect(aside.parentElement).toHaveClass("flex-col", "lg:flex-row");
  });

  it("?host=<id> oeffnet direkt einen Tab", async () => {
    const sessions: unknown[] = [];
    vi.stubGlobal("fetch", mockFetch(sessions));
    renderPage("/terminal?host=h-vm");
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(sessions).toEqual([{ host_id: "h-vm", cols: 120, rows: 30 }]);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["win-game"]);
  });

  it("baut wss:// hinter HTTPS", () => {
    expect(terminalSocketUrl("/api/v1/ws/terminal/x", { protocol: "https:", host: "deck.home.example" })).toBe(
      "wss://deck.home.example/api/v1/ws/terminal/x",
    );
  });
});
