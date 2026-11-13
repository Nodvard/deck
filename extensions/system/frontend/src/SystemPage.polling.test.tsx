import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { HostPicker } from "../../../_shared/frontend/src/HostPicker";
import { SystemPage, type SystemInfo } from "./SystemPage";
import type { LiveData } from "./LiveView";

const pickerRender = vi.hoisted(() => vi.fn());

vi.mock("../../../_shared/frontend/src/HostPicker", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../../_shared/frontend/src/HostPicker")>();
  return {
    ...original,
    HostPicker: (props: Parameters<typeof HostPicker>[0]) => {
      pickerRender(props.hosts);
      return <original.HostPicker {...props} />;
    },
  };
});

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
  vi.useFakeTimers();
  pickerRender.mockClear();
  window.history.replaceState({}, "", "/ext/system/system?host=h1");
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

it.each([200, 502])("zeichnet die Serverauswahl bei unverändertem Live-Status %i nicht neu", async (status) => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([
      { id: "h1", name: "nas", display_name: "NAS", os_family: "linux", credential: { kind: "ssh_key" } },
    ]));
    if (url.endsWith("/health")) return new Response(JSON.stringify({ data: [] }));
    return new Response(JSON.stringify(status === 200 ? LIVE : { detail: "SSH-Verbindung fehlgeschlagen" }), { status });
  });
  vi.stubGlobal("fetch", fetch);
  render(<SystemPage />);
  await act(async () => {});
  const calls = pickerRender.mock.calls.length;
  const tiles = pickerRender.mock.lastCall?.[0];
  if (status === 502) fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(fetch.mock.calls.filter(([input]) => String(input).endsWith("/live"))).toHaveLength(2);
  expect(pickerRender).toHaveBeenCalledTimes(calls);
  expect(pickerRender.mock.lastCall?.[0]).toBe(tiles);
});

it.each([200, 502])("behält die Serverkacheln bei unverändertem Info-Status %i bei", async (status) => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/hosts")) return new Response(JSON.stringify([
      { id: "h1", name: "nas", display_name: "NAS", os_family: "linux", credential: { kind: "ssh_key" } },
    ]));
    if (url.endsWith("/health")) return new Response(JSON.stringify({ data: [] }));
    if (url.endsWith("/live")) return new Response(JSON.stringify(LIVE));
    return new Response(JSON.stringify(status === 200 ? INFO : { detail: "SSH-Verbindung fehlgeschlagen" }), { status });
  });
  vi.stubGlobal("fetch", fetch);
  render(<SystemPage />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("tab", { name: "Zustand & Updates" }));
  await act(async () => {});
  const calls = pickerRender.mock.calls.length;
  const tiles = pickerRender.mock.lastCall?.[0];
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  await act(async () => {});
  expect(fetch.mock.calls.filter(([input]) => String(input).endsWith("/info"))).toHaveLength(2);
  expect(pickerRender.mock.calls.length).toBeGreaterThan(calls);
  expect(pickerRender.mock.calls.slice(calls).every(([hosts]) => hosts === tiles)).toBe(true);
});
