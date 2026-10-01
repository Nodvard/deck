import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { DemoSeedButton } from "./DemoSeedButton";

let calls: string[];
let seedStatus: number;

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
    status: "authenticated",
    mfaToken: null,
  });
}

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  return { invalidate, ...render(<QueryClientProvider client={client}><DemoSeedButton /></QueryClientProvider>) };
}

beforeEach(() => {
  calls = [];
  seedStatus = 200;
  login(["*"]);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${String(input).replace("/api/v1", "")}`);
    if (seedStatus === 409) {
      return new Response(JSON.stringify({ detail: "Beispieldaten gibt es nur auf einer frischen Installation ohne Server." }), { status: 409 });
    }
    return new Response(JSON.stringify({ active: true, created: true, hosts: 5, notifications: 4, layout_replaced: false, hosts_with_access: [] }));
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("DemoSeedButton", () => {
  it("legt die Beispieldaten an und lädt danach alle Ansichten neu", async () => {
    const { invalidate } = renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Mit Beispieldaten ansehen" }));
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(calls).toEqual(["POST /demo/seed"]);
  });

  it("zeigt den Grund, wenn es schon echte Server gibt (409)", async () => {
    seedStatus = 409;
    renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Mit Beispieldaten ansehen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("nur auf einer frischen Installation");
    expect(screen.getByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeEnabled();
  });

  it.each([
    [["hosts.read"]],
    [["hosts.read", "hosts.write"]],
    [["hosts.read", "settings.write"]],
    [[]],
  ])("ohne beide Rechte (%j) gibt es keinen Knopf", (permissions) => {
    login(permissions);
    renderButton();
    expect(screen.queryByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeNull();
  });

  it("mit hosts.write und settings.write erscheint er", () => {
    login(["hosts.read", "hosts.write", "settings.write"]);
    renderButton();
    expect(screen.getByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeInTheDocument();
  });
});
