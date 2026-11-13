import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SERVER_UNAVAILABLE_TEXT } from "../../../_shared/frontend/src/api";
import { UNREADABLE_ANSWER_TEXT } from "../../../_shared/frontend/src/loadError";
import { SystemPage, type SystemInfo } from "./SystemPage";
import { LiveView, type LiveData } from "./LiveView";
import { SystemQueryError } from "./systemQuery";

const LIVE: LiveData = {
  complete: true, interval_s: 1, uptime_s: 60,
  cpu: { model: null, cores: 1, total: null, per_core: [], load: [] },
  memory: null, temperatures: [], fans: [], disks: [], network: [], filesystems: [], processes: [],
  process_count: 0, throttled: null,
};

const INFO: SystemInfo = {
  host: { id: "h1", name: "NAS", address: "nas.test" },
  os: null, kernel: null, arch: null, hostname: null, uptime_s: null, load: [], cpus: null, cpu_percent: null,
  mem_total: null, mem_available: null, swap_total: null, swap_used: null,
  disks: [], failed_units: [], updates: null, reboot_required: false, temperature_c: null, findings: [],
};

beforeEach(() => {
  window.history.replaceState({}, "", "/ext/system/system");
  window.localStorage.clear();
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: () => true,
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("zeigt ohne Zugang einen eigenen neutralen Zustand trotz gespeichertem up", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
    String(input).endsWith("/hosts")
      ? [{ id: "h1", name: "nas", display_name: "NAS", os_family: "linux", status: "up", credential: null }]
      : { data: [] },
  ))));
  render(<SystemPage />);
  const tile = await screen.findByRole("button", { name: /NAS/ });
  expect(within(tile).getByRole("img", { name: "kein Zugang" })).not.toHaveClass("bg-emerald-400");
  expect(tile).toHaveTextContent("ohne SSH-Zugang");
});

it("zeigt erreichbar erst nach erfolgreicher Abfrage und trennt Befunde von Verbindungsfehlern", async () => {
  let resolveHealth!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/hosts")) return new Response(JSON.stringify(
      ["Fehler", "Befund"].map((name) => ({ id: name, name, display_name: name, os_family: "linux", status: "up", credential: { kind: "ssh_key" } })),
    ));
    return new Promise<Response>((resolve) => { resolveHealth = resolve; });
  }));
  render(<SystemPage />);
  const failed = await screen.findByRole("button", { name: /Fehler/ });
  expect(within(failed).getByRole("img", { name: "Status unbekannt" })).toBeInTheDocument();
  await act(async () => resolveHealth(new Response(JSON.stringify({ data: [
    { host_id: "Fehler", reachable: false, summary: "Anmeldung abgelehnt", tone: "danger" },
    { host_id: "Befund", reachable: true, summary: "Dienst ausgefallen", tone: "danger" },
  ] }))));
  expect(within(failed).getByRole("img", { name: "nicht erreichbar" })).toBeInTheDocument();
  expect(within(screen.getByRole("button", { name: /Befund/ })).getByRole("img", { name: "erreichbar" })).toBeInTheDocument();
});

it("stoppt nach einem Live-Fehler bis zum erneuten Versuch und nennt die Zugangseinstellungen", async () => {
  vi.useFakeTimers();
  const fetchLive = vi.fn<() => Promise<LiveData>>().mockRejectedValue(new SystemQueryError("Anmeldung abgelehnt", 502));
  render(<LiveView hostId="h1" fetchLive={fetchLive} />);
  await act(async () => {});
  expect(screen.getByRole("alert")).toHaveTextContent("Anmeldung abgelehnt");
  expect(screen.getByRole("alert")).toHaveTextContent("Einstellungen → Server & Zugänge");
  expect(screen.getByRole("link", { name: "Server & Zugänge öffnen" })).toHaveAttribute("href", "/settings/hosts");
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(fetchLive).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  await act(async () => {});
  expect(fetchLive).toHaveBeenCalledTimes(2);
});

it("hält auch nach erfolgreichen Messungen an und setzt nach erfolgreichem Wiederholen den Takt fort", async () => {
  vi.useFakeTimers();
  const fetchLive = vi.fn<() => Promise<LiveData>>()
    .mockResolvedValueOnce(LIVE)
    .mockRejectedValueOnce(new Error("Zeitüberschreitung"))
    .mockResolvedValue(LIVE);
  render(<LiveView hostId="h1" fetchLive={fetchLive} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByRole("alert")).toHaveTextContent("Zeitüberschreitung");
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(fetchLive).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  await act(async () => {});
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(fetchLive).toHaveBeenCalledTimes(4);
});

it("beginnt beim Serverwechsel nach einem Fehler wieder mit der Messung", async () => {
  vi.useFakeTimers();
  const fetchLive = vi.fn<(id: string) => Promise<LiveData>>()
    .mockRejectedValueOnce(new Error("Anmeldung abgelehnt"))
    .mockResolvedValue(LIVE);
  const view = render(<LiveView hostId="h1" fetchLive={fetchLive} />);
  await act(async () => {});
  expect(screen.getByRole("alert")).toBeInTheDocument();
  view.rerender(<LiveView hostId="h2" fetchLive={fetchLive} />);
  await act(async () => {});
  expect(fetchLive).toHaveBeenLastCalledWith("h2");
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(fetchLive).toHaveBeenCalledTimes(3);
});

it("ersetzt nach einem Live-Fehler auch einen zuvor erfolgreichen Verbindungsstatus", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([
      { id: "h1", name: "nas", display_name: "NAS", os_family: "linux", status: "up", credential: { kind: "ssh_key" } },
    ]));
    if (url.endsWith("/health")) return new Response(JSON.stringify({ data: [{ host_id: "h1", reachable: true, summary: "alles in Ordnung", tone: "good" }] }));
    return new Response(JSON.stringify({ detail: "Anmeldung abgelehnt" }), { status: 502 });
  }));
  render(<SystemPage />);
  fireEvent.click(await screen.findByRole("button", { name: /NAS/ }));
  await screen.findByText(/Anmeldung abgelehnt/);
  await waitFor(() => expect(within(screen.getByTestId("host-picker-selected")).getByRole("img", { name: "nicht erreichbar" })).toBeInTheDocument());
  expect(screen.getByTestId("host-picker-selected")).not.toHaveTextContent("alles in Ordnung");
});

it("übernimmt auch fehlgeschlagene Zustandsabfragen in die Erreichbarkeitsanzeige", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([
      { id: "h1", name: "nas", display_name: "NAS", os_family: "linux", status: "up", credential: { kind: "ssh_key" } },
    ]));
    if (url.endsWith("/health")) return new Response(JSON.stringify({ data: [] }));
    if (url.endsWith("/live")) return new Response(JSON.stringify(LIVE));
    return new Response(JSON.stringify({ detail: "SSH-Verbindung fehlgeschlagen" }), { status: 502 });
  }));
  render(<SystemPage />);
  fireEvent.click(await screen.findByRole("button", { name: /NAS/ }));
  await waitFor(() => expect(within(screen.getByTestId("host-picker-selected")).getByRole("img", { name: "erreichbar" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("tab", { name: "Zustand & Updates" }));
  await screen.findByText(/SSH-Verbindung fehlgeschlagen/);
  expect(within(screen.getByTestId("host-picker-selected")).getByRole("img", { name: "nicht erreichbar" })).toBeInTheDocument();
});

it.each([200, 502])("verwirft den Status einer verspäteten Live-Antwort mit HTTP %i nach dem Tabwechsel", async (liveStatus) => {
  let resolveLive!: (response: Response) => void;
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([
      { id: "h1", name: "nas", display_name: "NAS", os_family: "linux", credential: { kind: "ssh_key" } },
    ]));
    if (url.endsWith("/health")) return new Response(JSON.stringify({ data: [] }));
    if (url.endsWith("/live")) return new Promise<Response>((resolve) => { resolveLive = resolve; });
    return liveStatus === 200
      ? new Response(JSON.stringify({ detail: "SSH-Verbindung fehlgeschlagen" }), { status: 502 })
      : new Response(JSON.stringify(INFO));
  });
  vi.stubGlobal("fetch", fetch);
  render(<SystemPage />);
  fireEvent.click(await screen.findByRole("button", { name: /NAS/ }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/live$/), expect.anything()));
  fireEvent.click(screen.getByRole("tab", { name: "Zustand & Updates" }));
  if (liveStatus === 200) await screen.findByText(/SSH-Verbindung fehlgeschlagen/);
  else await screen.findByTestId("system-info");
  const status = liveStatus === 200 ? "nicht erreichbar" : "erreichbar";
  expect(within(screen.getByTestId("host-picker-selected")).getByRole("img", { name: status })).toBeInTheDocument();
  await act(async () => resolveLive(new Response(JSON.stringify(
    liveStatus === 200 ? LIVE : { detail: "SSH-Verbindung fehlgeschlagen" },
  ), { status: liveStatus })));
  expect(within(screen.getByTestId("host-picker-selected")).getByRole("img", { name: status })).toBeInTheDocument();
});

const FAILURES = [
  ...[400, 401, 403, 404, 409, 500, 503].map((status) => ({
    name: `HTTP ${status}`,
    text: `Abfrage abgelehnt (${status})`,
    reply: () => new Response(JSON.stringify({ detail: `Abfrage abgelehnt (${status})` }), { status }),
  })),
  { name: "ungültiges JSON", text: UNREADABLE_ANSWER_TEXT, reply: () => new Response("{") },
  { name: "Netzwerkfehler zum Dashboard", text: SERVER_UNAVAILABLE_TEXT, reply: () => { throw new TypeError("Failed to fetch"); } },
  { name: "Zeitüberschreitung zum Dashboard", text: "TimeoutError: Zeitüberschreitung", reply: () => { throw new DOMException("Zeitüberschreitung", "TimeoutError"); } },
  { name: "Proxyfehler", text: SERVER_UNAVAILABLE_TEXT, reply: () => new Response("Bad Gateway", { status: 502 }) },
];

describe.each(["live", "info"] as const)("%s-Abfrage", (view) => {
  it.each(FAILURES.flatMap((failure) => [true, undefined].map((reachable) => ({ ...failure, reachable }))))(
    "$name verändert den SSH-Status $reachable nicht",
    async ({ reply, text, reachable, name }) => {
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/hosts")) return new Response(JSON.stringify([
          { id: "h1", name: "nas", display_name: "NAS", os_family: "linux", status: "up", credential: { kind: "ssh_key" } },
        ]));
        if (url.endsWith("/health")) return new Response(JSON.stringify({
          data: reachable === undefined ? [] : [{ host_id: "h1", reachable, summary: "alles in Ordnung", tone: "good" }],
        }));
        if (url.endsWith(`/${view}`)) return reply();
        return new Promise<Response>(() => {});
      }));
      render(<SystemPage />);
      const status = reachable ? "erreichbar" : "Status unbekannt";
      const tile = await screen.findByRole("button", { name: /NAS/ });
      await waitFor(() => expect(within(tile).getByRole("img", { name: status })).toBeInTheDocument());
      fireEvent.click(tile);
      if (view === "info") fireEvent.click(screen.getByRole("tab", { name: "Zustand & Updates" }));
      await screen.findByText((_, element) => element?.tagName === "P" && element.textContent === `${view === "live" ? "Live-Abfrage angehalten" : "Fehler"}: ${text}`);
      expect(within(screen.getByTestId("host-picker-selected")).getByRole("img", { name: status })).toBeInTheDocument();
      expect(screen.getByTestId("host-picker-selected")).not.toHaveTextContent("SSH-Abfrage fehlgeschlagen");
      if (name !== "HTTP 409") expect(screen.queryByText(/Prüfe den SSH-Zugang/)).toBeNull();
    },
  );
});

it("verweist bei fehlendem Zugang auf die Zugangseinstellungen", async () => {
  const fetchLive = vi.fn<() => Promise<LiveData>>().mockRejectedValue(new SystemQueryError("Kein SSH-Zugang eingerichtet", 409));
  render(<LiveView hostId="h1" fetchLive={fetchLive} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Kein SSH-Zugang eingerichtet");
  expect(screen.getByRole("link", { name: "Server & Zugänge öffnen" })).toHaveAttribute("href", "/settings/hosts");
});

it("nennt bei allgemeinen Live-Fehlern keinen SSH-Zugang als Ursache", async () => {
  const fetchLive = vi.fn<() => Promise<LiveData>>().mockRejectedValue(new Error("Interner Fehler"));
  render(<LiveView hostId="h1" fetchLive={fetchLive} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Interner Fehler");
  expect(screen.getByRole("alert")).toHaveTextContent("Versuche die Abfrage erneut.");
  expect(screen.queryByText(/Prüfe den SSH-Zugang/)).toBeNull();
  expect(screen.queryByRole("link", { name: "Server & Zugänge öffnen" })).toBeNull();
});
