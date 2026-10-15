import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { DesignNotice } from "./DesignNotice";

let preferences: Record<string, unknown>;
let patches: unknown[];
let patchFails: boolean;

function installFetch() {
  patches = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (url === "/me/preferences" && method === "GET") return new Response(JSON.stringify(preferences));
    if (url === "/me/preferences" && method === "PATCH") {
      const body = JSON.parse(String(init?.body));
      patches.push(body);
      if (patchFails) return new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 });
      preferences = { ...preferences, ...body };
      return new Response(JSON.stringify(preferences));
    }
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
}

function renderNotice() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><DesignNotice /></QueryClientProvider>);
}

beforeEach(() => {
  preferences = { first_steps_dismissed: false, theme: "simple", design_notice_dismissed: false };
  patchFails = false;
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "admin", display_name: "Admin", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
  installFetch();
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
  window.localStorage.clear();
});

describe("DesignNotice", () => {
  it("„Zum bisherigen Aussehen“ schaltet auf „Bisher“ und merkt es beim Benutzer", async () => {
    renderNotice();
    expect(await screen.findByText("Deck hat ein neues Aussehen.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zum bisherigen Aussehen" }));
    expect(document.documentElement.dataset.theme).toBe("original");
    await waitFor(() => expect(patches).toEqual([{ design_notice_dismissed: true, theme: "original" }]));
    expect(screen.queryByTestId("design-notice")).toBeNull();
  });

  it("„Behalten“ lässt das Aussehen und blendet den Hinweis aus", async () => {
    document.documentElement.dataset.theme = "simple";
    renderNotice();
    fireEvent.click(await screen.findByRole("button", { name: "Behalten" }));
    await waitFor(() => expect(patches).toEqual([{ design_notice_dismissed: true }]));
    expect(document.documentElement.dataset.theme).toBe("simple");
    expect(screen.queryByTestId("design-notice")).toBeNull();
  });

  it("scheitert das Speichern, bleibt der Hinweis trotzdem weg", async () => {
    patchFails = true;
    renderNotice();
    fireEvent.click(await screen.findByRole("button", { name: "Behalten" }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(screen.queryByTestId("design-notice")).toBeNull();
  });

  it("schon erledigt oder ein älterer Server ohne das Feld: kein Hinweis", async () => {
    for (const value of [true, undefined]) {
      preferences = { first_steps_dismissed: false, theme: "simple", design_notice_dismissed: value };
      vi.mocked(fetch).mockClear();
      const { unmount } = renderNotice();
      // Die Antwort abwarten: ohne Hinweis bleibt nichts gerendert.
      await waitFor(() => expect(fetch).toHaveBeenCalled());
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(screen.queryByTestId("design-notice")).toBeNull();
      unmount();
    }
  });
});
