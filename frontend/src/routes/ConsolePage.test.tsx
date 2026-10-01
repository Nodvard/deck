import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { ConsolePage, LAYOUT_STORAGE_KEY, consoleSocketUrl } from "./ConsolePage";

/**
 * noVNC selbst ist hier ein Test-Double: jsdom hat weder Canvas-Rendering noch echte
 * WebSockets. Geprueft wird der Vertrag der Seite -- was sie an noVNC uebergibt
 * (Nodvard-Deck-WS-URL statt Hypervisor, Einmal-Kennwort als RFB-Credentials) und wie sie
 * auf dessen Ereignisse reagiert. Die echte Byte-Kette ist Backend-seitig bewiesen
 * (test_console_api.py, test_ext_proxmox.py) und live im Browser.
 */
class FakeRfb extends EventTarget {
  static instances: FakeRfb[] = [];
  scaleViewport = false;
  background = "";
  constructor(
    public target: Element,
    public url: string,
    public options: { credentials?: { password: string }; wsProtocols?: string[] },
  ) {
    super();
    FakeRfb.instances.push(this);
  }
  focus = vi.fn();
  disconnect = vi.fn();
  sendCtrlAltDel = vi.fn();
  sendCredentials = vi.fn();
  sendKey = vi.fn();
  emit(type: string, detail: unknown = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

vi.mock("@novnc/novnc", () => ({ default: FakeRfb }));

function mockFetch(opts: { consoleStatus?: number; onOpen?: (body: unknown) => void } = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/v1/hosts/h-vm") && method === "GET") {
      return new Response(JSON.stringify({ id: "h-vm", name: "proxmox-pve2-vm-110", display_name: "game-win", kind: "vm", status: "up" }));
    }
    if (url.endsWith("/api/v1/console/sessions") && method === "POST") {
      opts.onOpen?.(JSON.parse(init?.body as string));
      if (opts.consoleStatus && opts.consoleStatus !== 200) {
        return new Response(JSON.stringify({ detail: "Konsole konnte nicht geöffnet werden: HTTP 403: VM.Console" }), {
          status: opts.consoleStatus,
        });
      }
      return new Response(JSON.stringify({ session_id: "s1", ws_url: "/api/v1/ws/console/s1", protocol: "vnc", password: "gen12345" }));
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/console/h-vm"]}>
      <Routes>
        <Route path="/console/:hostId" element={<ConsolePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  FakeRfb.instances = [];
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "owner1", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
});

describe("ConsolePage", () => {
  it("oeffnet die Sitzung ueber Nodvard Deck und uebergibt noVNC nur dessen URL + Einmal-Kennwort", async () => {
    const opened: unknown[] = [];
    vi.stubGlobal("fetch", mockFetch({ onOpen: (b) => opened.push(b) }));
    renderPage();

    await waitFor(() => expect(FakeRfb.instances).toHaveLength(1));
    const rfb = FakeRfb.instances[0];
    expect(opened).toEqual([{ host_id: "h-vm" }]);
    expect(rfb.url).toBe(`ws://${window.location.host}/api/v1/ws/console/s1`);
    expect(rfb.options.credentials?.password).toBe("gen12345");
    expect(rfb.options.wsProtocols).toEqual(["binary"]);
    expect(rfb.scaleViewport).toBe(true);

    expect(await screen.findByText("Konsole: game-win")).toBeTruthy();
    expect(screen.getByText("Verbinde …")).toBeTruthy();

    act(() => rfb.emit("connect"));
    expect(screen.getByText("Verbunden")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Strg+Alt+Entf" }));
    expect(rfb.sendCtrlAltDel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Originalgröße" }));
    expect(rfb.scaleViewport).toBe(false);
  });

  it("zeigt die Meldung des Backends, wenn die Konsole nicht geoeffnet werden kann", async () => {
    vi.stubGlobal("fetch", mockFetch({ consoleStatus: 502 }));
    renderPage();

    expect(await screen.findByText(/VM\.Console/)).toBeTruthy();
    expect(screen.getByText("Fehler")).toBeTruthy();
    expect(FakeRfb.instances).toHaveLength(0);
  });

  it("unerwarteter Abbruch wird als Fehler gemeldet, Neu verbinden baut eine frische Sitzung", async () => {
    let opens = 0;
    vi.stubGlobal("fetch", mockFetch({ onOpen: () => (opens += 1) }));
    renderPage();
    await waitFor(() => expect(FakeRfb.instances).toHaveLength(1));
    const first = FakeRfb.instances[0];

    act(() => first.emit("connect"));
    act(() => first.emit("disconnect", { clean: false }));
    expect(screen.getByText("Verbindung unerwartet getrennt.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Neu verbinden" }));
    await waitFor(() => expect(FakeRfb.instances).toHaveLength(2));
    expect(first.disconnect).toHaveBeenCalled();
    expect(opens).toBe(2);

    // Das spaete `disconnect` der ALTEN Sitzung darf den Status der neuen nicht ueberschreiben.
    act(() => FakeRfb.instances[1].emit("connect"));
    act(() => first.emit("disconnect", { clean: true }));
    expect(screen.getByText("Verbunden")).toBeTruthy();
  });

  it("baut wss:// hinter HTTPS", () => {
    expect(consoleSocketUrl("/api/v1/ws/console/x", { protocol: "https:", host: "deck.home.example" })).toBe(
      "wss://deck.home.example/api/v1/ws/console/x",
    );
  });

  it("Text senden: tippt den Text als Tastendruecke im gewaehlten Layout", async () => {
    vi.stubGlobal("fetch", mockFetch());
    try { window.localStorage.removeItem(LAYOUT_STORAGE_KEY); } catch { /* egal */ }
    renderPage();
    await waitFor(() => expect(FakeRfb.instances).toHaveLength(1));
    const rfb = FakeRfb.instances[0];
    act(() => rfb.emit("connect"));

    fireEvent.click(screen.getByRole("button", { name: "Text senden …" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Zu sendender Text"), { target: { value: "zY" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Tippen" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // Deutsches Layout (Standard): "z" liegt physisch auf KeyY, "Y" = Umschalt + KeyZ.
    expect(rfb.sendKey.mock.calls.map(([, code, down]) => `${down ? "+" : "-"}${code}`)).toEqual([
      "+KeyY", "-KeyY", "+ShiftLeft", "+KeyZ", "-KeyZ", "-ShiftLeft",
    ]);
  });

  it("merkt das Layout unter dem neuen Schluessel nodvard-deck.console.layout und fasst den alten nicht an", async () => {
    expect(LAYOUT_STORAGE_KEY).toBe("nodvard-deck.console.layout");
    vi.stubGlobal("fetch", mockFetch());
    try {
      window.localStorage.removeItem("lattice.console.layout");
      window.localStorage.setItem(LAYOUT_STORAGE_KEY, "us"); // wie nach migrateLegacyStorage() (main.tsx)
      renderPage();
      await waitFor(() => expect(FakeRfb.instances).toHaveLength(1));
      act(() => FakeRfb.instances[0].emit("connect"));

      fireEvent.click(screen.getByRole("button", { name: "Text senden …" }));
      const dialog = await screen.findByRole("dialog");
      const select = within(dialog).getByLabelText("Layout im Gast") as HTMLSelectElement;
      expect(select.value).toBe("us");

      fireEvent.change(select, { target: { value: "de" } });
      expect(window.localStorage.getItem(LAYOUT_STORAGE_KEY)).toBe("de");
      expect(window.localStorage.getItem("lattice.console.layout")).toBeNull();
    } finally {
      window.localStorage.removeItem(LAYOUT_STORAGE_KEY);
    }
  });

  it("Text senden: nicht tippbare Zeichen und ungewollte Zeilenumbrueche werden abgelehnt, nichts wird geschickt", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderPage();
    await waitFor(() => expect(FakeRfb.instances).toHaveLength(1));
    const rfb = FakeRfb.instances[0];
    act(() => rfb.emit("connect"));

    fireEvent.click(screen.getByRole("button", { name: "Text senden …" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Zu sendender Text");

    fireEvent.change(input, { target: { value: "rm -rf /tmp/x\n" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Tippen" }));
    expect(await within(dialog).findByText(/enthält Zeilenumbrüche/)).toBeTruthy();

    fireEvent.change(input, { target: { value: "Häkchen ✓" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Tippen" }));
    expect(await within(dialog).findByText(/Nicht tippbar im Layout Deutsch \(QWERTZ\): ✓/)).toBeTruthy();
    expect(rfb.sendKey).not.toHaveBeenCalled();
  });
});
