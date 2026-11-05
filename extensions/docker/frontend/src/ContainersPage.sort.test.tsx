import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ContainersPage } from "./ContainersPage";

const SERVICES = [
  { id: "h:a", name: "Zeta", state: "running", status: "Up 2 days" },
  { id: "h:b", name: "Öl", state: "paused", status: "Up 10 hours (Paused)" },
  { id: "h:c", name: "Ähre", state: "exited", status: "Exited (0) 1 hour ago" },
  { id: "h:d", name: "Beta", state: "running", status: "Up 2 hours" },
  { id: "h:e", name: "Apfel", state: "running", status: "Up 2 days" },
].map((s) => ({ ...s, host: "Demo", host_id: "h", container: s.name, tone: "neutral", url: null }));

const STATS = {
  data: {
    "h:a": { cpu_percent: 10, mem_used: 1024, mem_limit: null },
    "h:b": { cpu_percent: 2, mem_used: 1048576, mem_limit: null },
    "h:d": { cpu_percent: 0, mem_used: 0, mem_limit: null },
    "h:e": { cpu_percent: null, mem_used: null, mem_limit: null },
  },
  started_at: {
    "h:a": "2026-10-31T00:00:00Z",
    "h:b": "2026-11-01T23:00:00Z",
    "h:d": "2026-11-02T00:30:00+01:00",
    "h:e": "2026-10-31T00:00:00Z",
  },
};

beforeEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/ext/docker/matrix");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: () => true,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES, meta: {} }));
    if (url.includes("/stats")) return new Response(JSON.stringify(STATS));
    if (url.includes("/image-updates")) return new Response(JSON.stringify({ data: {}, hosts: {} }));
    throw new Error(url);
  }));
});

const order = () => screen.getAllByTestId(/^row-/).map((row) => row.getAttribute("data-testid")!.slice(6));
const header = (name: string) => screen.getByRole("columnheader", { name });
const click = (name: string) => fireEvent.click(within(header(name)).getByRole("button", { name }));

async function open() {
  render(<ContainersPage />);
  await waitFor(() => expect(screen.getByTestId("cpu-h:a")).toHaveTextContent("10.0 %"));
}

it("sortiert nach deutschem Zustand und schaltet zur ursprünglichen Reihenfolge zurück", async () => {
  await open();
  expect(order()).toEqual(["a", "b", "c", "d", "e"]);
  click("Zustand");
  expect(order()).toEqual(["c", "a", "d", "e", "b"]);
  expect(header("Zustand")).toHaveAttribute("aria-sort", "ascending");
  expect(header("Zustand")).toHaveTextContent("↑");
  click("Zustand");
  expect(order()).toEqual(["b", "a", "d", "e", "c"]);
  expect(header("Zustand")).toHaveAttribute("aria-sort", "descending");
  expect(header("Zustand")).toHaveTextContent("↓");
  click("Zustand");
  expect(order()).toEqual(["a", "b", "c", "d", "e"]);
  expect(header("Zustand")).not.toHaveAttribute("aria-sort");
  expect(header("Zustand")).not.toHaveTextContent(/[↑↓]/);
});

it("sortiert Laufzeit nach Startzeit samt Zeitzone, bei Gleichstand stabil und ohne Wert zuletzt", async () => {
  await open();
  click("Status/Laufzeit");
  expect(order()).toEqual(["d", "b", "a", "e", "c"]);
  click("Status/Laufzeit");
  expect(order()).toEqual(["a", "e", "b", "d", "c"]);
  click("Status/Laufzeit");
  expect(order()).toEqual(["a", "b", "c", "d", "e"]);
});

it("behandelt ungültige und nie gesetzte Startzeiten als fehlend", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: SERVICES }));
    if (url.includes("/stats")) return new Response(JSON.stringify({ ...STATS, started_at: {
      ...STATS.started_at, "h:a": "unbekannt", "h:e": "0001-01-01T00:00:00Z",
    } }));
    if (url.includes("/image-updates")) return new Response(JSON.stringify({ data: {}, hosts: {} }));
    throw new Error(url);
  });
  await open();
  click("Status/Laufzeit");
  expect(order()).toEqual(["d", "b", "a", "c", "e"]);
  click("Status/Laufzeit");
  expect(order()).toEqual(["b", "d", "a", "c", "e"]);
});

it("sortiert innerhalb der Servergruppen und behält Suche und Sortierung beim Aktualisieren", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/widgets/matrix")) return new Response(JSON.stringify({ data: [
      ...SERVICES, ...SERVICES.map((s) => ({ ...s, id: s.id.replace("h:", "i:"), host: "Zweiter", host_id: "i" })),
    ] }));
    if (url.includes("/stats")) return new Response(JSON.stringify(STATS));
    if (url.includes("/image-updates")) return new Response(JSON.stringify({ data: {}, hosts: {} }));
    throw new Error(url);
  });
  await open();
  fireEvent.click(within(screen.getAllByRole("columnheader", { name: "Container" })[0]).getByRole("button"));
  const orders = () => screen.getAllByRole("table").map((t) => within(t).getAllByTestId(/^row-/)
    .map((row) => row.getAttribute("data-testid")!.slice(6)));
  expect(orders()).toEqual([["c", "e", "d", "b", "a"], ["c", "e", "d", "b", "a"]]);
  fireEvent.change(screen.getByRole("textbox", { name: "Container suchen" }), { target: { value: "a" } });
  expect(orders()).toEqual([["e", "d", "a"], ["e", "d", "a"]]);
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/widgets/matrix"))).toHaveLength(2));
  expect(orders()).toEqual([["e", "d", "a"], ["e", "d", "a"]]);
});

it.each([
  ["Container", ["c", "e", "d", "b", "a"], ["a", "b", "d", "e", "c"]],
  ["CPU", ["d", "b", "a", "c", "e"], ["a", "b", "d", "c", "e"]],
  ["RAM", ["d", "a", "b", "c", "e"], ["b", "a", "d", "c", "e"]],
])("sortiert %s und beginnt beim Spaltenwechsel aufsteigend", async (spalte, auf, ab) => {
  await open();
  click("Zustand");
  click("Zustand");
  click(spalte as string);
  expect(order()).toEqual(auf);
  expect(header(spalte as string)).toHaveAttribute("aria-sort", "ascending");
  expect(header("Zustand")).not.toHaveAttribute("aria-sort");
  click(spalte as string);
  expect(order()).toEqual(ab);
});
