import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { StackOut } from "./Stacks";

const requests: string[] = [];

beforeEach(() => {
  requests.length = 0;
  window.history.replaceState({}, "", "/ext/docker/matrix?tab=stacks");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: () => true,
  };
  const stacks: StackOut[] = ["shop", "leer"].map((project) => ({
    project, state: "running", running: 1, problems: 0, total: 1, containers: [], source: "compose",
    working_dir: `/opt/stacks/${project}`, config_files: [`/opt/stacks/${project}/compose.yaml`], is_self: false,
    changes: { changed: [], added: [], removed: [] }, changed: false, check_error: null, adopted: true, adoption_note: null,
  }));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url}`);
    if (url.endsWith("/ext/docker/hosts")) return Response.json({ hosts: [{ id: "h1", name: "Server" }] });
    if (url.endsWith("/stacks")) return Response.json({ host_id: "h1", stacks, check_error: null });
    if (url.endsWith("/stacks/usage")) return Response.json({ host_id: "h1", usage: {} });
    if (url.endsWith("/update")) return Response.json({ services: [], updates: [], data_store: false, rollback: [] });
    if (url.endsWith("/git")) return Response.json({ source: null, accesses: [], layout_problem: null });
    if (url.endsWith("/git/applied")) return Response.json({ applied: null, previous: null });
    if (url.endsWith("/actions/docker.stack_up")) return new Promise<Response>(() => {});
    if (url.includes("/widgets/matrix")) return Response.json({ data: [], meta: {} });
    if (url.includes("/image-updates")) return Response.json({ data: {}, hosts: {} });
    if (url.includes("/ext/docker/stats")) return Response.json({ data: {} });
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

async function renderBundle() {
  const bundlePath = "../dist/index.js";
  const { ContainersPage }: typeof import("./ContainersPage") = await import(bundlePath);
  render(<ContainersPage />);
}

it("zeigt im Bundle die Stack-Aktionen und sperrt Images holen bei laufender Aktion", async () => {
  await renderBundle();
  const shop = await screen.findByTestId("stack-shop");
  expect(shop).toHaveClass("rounded-lg", "sm:border-0");
  const primary = within(shop).getAllByRole("button").filter((button) => !button.closest("details"));
  expect(primary.map((button) => button.textContent)).toEqual(["Anwenden", "Bearbeiten", "Update"]);
  fireEvent.click(within(shop).getByText("Mehr"));
  expect(within(shop).getByRole("button", { name: "Stoppen" })).toBeInTheDocument();
  expect(within(shop).queryByRole("button", { name: "Images holen" })).toBeNull();
  fireEvent.click(within(shop).getByRole("button", { name: "Anwenden: shop" }));
  await waitFor(() => expect(requests).toContain("POST /api/v1/hosts/h1/actions/docker.stack_up"));
  fireEvent.click(within(screen.getByTestId("stack-leer")).getByRole("button", { name: "Update" }));
  const panel = await screen.findByTestId("update-leer");
  const pull = within(panel).getByRole("button", { name: "Images holen" });
  expect(pull).toBeDisabled();
  fireEvent.click(pull);
  expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalledOnce();
  expect(requests.filter((request) => request.startsWith("POST"))).toEqual(["POST /api/v1/hosts/h1/actions/docker.stack_up"]);
});

it("sperrt im Bundle Änderungen an Git-Quellen ohne Verwaltungsrecht", async () => {
  window.__nodvardDeck.hasPermission = (permission) => permission !== "extensions.manage";
  await renderBundle();
  const shop = await screen.findByTestId("stack-shop");
  fireEvent.click(within(shop).getByText("Mehr"));
  fireEvent.click(within(shop).getByRole("button", { name: "Aus Git" }));
  expect(await screen.findByText("Die Quelle dürfen nur Administratoren ändern.")).toBeInTheDocument();
  for (const label of ["Adresse des Repositorys", "Branch oder Tag", "Compose-Datei im Repository", "Zugang"]) {
    expect(screen.getByLabelText(label)).toBeDisabled();
  }
  const save = screen.getByRole("button", { name: "Eintragen und holen" });
  expect(save).toBeDisabled();
  fireEvent.click(save);
  expect(requests.every((request) => request.startsWith("GET"))).toBe(true);
});
