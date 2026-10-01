import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../components/GlobalDialogs";
import { respond } from "../preview/fixtures";
import { useAuthStore } from "../state/auth";
import { useDialogsStore } from "../state/dialogs";
import { HostPage } from "./HostPage";

/**
 * Wie im echten Router: eine Route fuer alle Server, der globale Dialog (`GlobalDialogs`)
 * haengt ausserhalb und bleibt bei einem Serverwechsel offen -- anders als `HostPage.test.tsx`
 * hier mit dem ECHTEN Dialog-Zustand statt einer Attrappe.
 */
describe("HostPage: offene Rueckfrage beim Serverwechsel", () => {
  const posts: string[] = [];

  beforeEach(() => {
    posts.length = 0;
    useDialogsStore.setState({ confirmPending: null, promptPending: null });
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
      status: "authenticated",
      mfaToken: null,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      const method = init?.method ?? "GET";
      if (method === "POST") posts.push(path.replace(/^\/api\/v1/, ""));
      return new Response(JSON.stringify(respond(path, method) ?? {}), { status: 200 });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("nach dem Wechsel zu einem anderen Server bestaetigt: keine Aktion auf dem alten Server", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/hosts/g-docker"]}>
          <Link to="/hosts/g-monitoring">Wechsel zu monitoring</Link>
          <Routes>
            <Route path="/hosts/:hostId" element={<HostPage />} />
          </Routes>
          <GlobalDialogs />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Neustarten" }));
    await vi.waitFor(() => expect(useDialogsStore.getState().confirmPending).not.toBeNull());

    fireEvent.click(screen.getByRole("link", { name: "Wechsel zu monitoring", hidden: true }));
    await screen.findByRole("heading", { level: 1, name: /monitoring/, hidden: true });
    await act(async () => {
      useDialogsStore.getState().settleConfirm(true);
    });

    expect(posts).toEqual([]);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
