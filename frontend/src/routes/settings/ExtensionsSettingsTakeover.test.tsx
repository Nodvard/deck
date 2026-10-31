import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { useAuthStore } from "../../state/auth";
import { ExtensionsSettings, takeoverAllowedText } from "./ExtensionsSettings";

const REQUEST = { id: "renamed-ext", old_id: "old-ext", name: "Neu", source: "pip", permissions: ["schedule.register", "hosts.execute"] };
const OLD = {
  id: "old-ext", version: "0.1.0", state: "enabled", source: "pip", name: "Alt", description: null, icon: null,
  granted_permissions: [], last_error: null, takeover_requests: [REQUEST],
};

let rows: unknown[];
let posted: unknown[];
let answer: Response | null;

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
  rows = [OLD];
  posted = [];
  answer = null;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    if (method === "GET" && path === "/extensions") return new Response(JSON.stringify(rows));
    if (method === "POST" && path === "/extensions/renamed-ext/takeover") {
      posted.push(JSON.parse(String(init?.body)));
      rows = [{ ...OLD, takeover_requests: [{ ...REQUEST, allowed: true }] }];
      return answer ?? new Response(JSON.stringify({ restart_needed: true, switched_off: true, extra_permissions: ["hosts.execute"] }));
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  }));
});

afterEach(async () => {
  const cancel = screen.queryByRole("button", { name: "Abbrechen" });
  if (cancel) fireEvent.click(cancel);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><ExtensionsSettings /><GlobalDialogs /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Übernahme durch eine Erweiterung mit alter Kennung", () => {
  it("fragt mit Rechten und Rückweg nach und meldet, dass sie erst nach dem Neustart gilt", async () => {
    renderPage();
    const box = await screen.findByTestId("takeover-renamed-ext");
    expect(box.textContent).toContain("„Neu“ will die gespeicherten Daten dieses Moduls übernehmen");

    fireEvent.click(within(box).getByRole("button", { name: "Übernahme erlauben" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("ein nachinstalliertes Paket");
    expect(dialog.textContent).toContain("schedule.register, hosts.execute");
    expect(dialog.textContent).toContain("Bis zum nächsten Neustart kannst du es noch zurücknehmen");
    fireEvent.click(within(dialog).getByRole("button", { name: "Übernahme erlauben" }));

    expect(await screen.findByText(/Übernahme durch „Neu“ erlaubt/)).toBeTruthy();
    expect(posted).toEqual([{ old_id: "old-ext" }]);
    await waitFor(() => expect(screen.getByTestId("takeover-renamed-ext").textContent).toContain("bis dahin läuft keine"));
    expect(within(screen.getByTestId("takeover-renamed-ext")).queryByRole("button")).toBeNull();
  });

  it("schickt nichts, wenn man abbricht", async () => {
    renderPage();
    fireEvent.click(within(await screen.findByTestId("takeover-renamed-ext")).getByRole("button", { name: "Übernahme erlauben" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted).toEqual([]);
  });

  it("zeigt den Grund, wenn der Server ablehnt", async () => {
    answer = new Response(JSON.stringify({ detail: "Die alte Kennung „old-ext“ gehört schon zu „docker“." }), { status: 409 });
    renderPage();
    fireEvent.click(within(await screen.findByTestId("takeover-renamed-ext")).getByRole("button", { name: "Übernahme erlauben" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Übernahme erlauben" }));
    expect(await screen.findByText(/gehört schon zu „docker“/)).toBeTruthy();
  });

  it("ohne wartende Übernahme gibt es keinen Hinweis", async () => {
    rows = [{ ...OLD, takeover_requests: undefined }];
    renderPage();
    await screen.findByTestId("ext-old-ext");
    expect(screen.queryByTestId("takeover-renamed-ext")).toBeNull();
  });
});

describe("takeoverAllowedText", () => {
  it("nennt den Neustart und bei mehr Rechten, dass sie aus bleibt", () => {
    expect(takeoverAllowedText("Neu", "Alt", [])).toBe("Übernahme durch „Neu“ erlaubt. Sie gilt nach dem nächsten Neustart von Nodvard Deck.");
    expect(takeoverAllowedText("Neu", "Alt", ["hosts.execute"])).toContain("mehr Rechte, als „Alt“ hatte (hosts.execute), und bleibt deshalb aus");
  });
});
