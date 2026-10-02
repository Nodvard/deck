import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockHostsApi, reply } from "../../../test/hostsMock";
import { useAuthStore } from "../../../state/auth";
import { HostKeysCard } from "./HostKeysCard";

const SWITCH = "Neue Server-Schlüssel erst nach meiner Bestätigung merken";

function setUser(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
  });
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><HostKeysCard /></QueryClientProvider>);
}

beforeEach(() => setUser(["*"]));
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Karte „Neue Server-Schlüssel“", () => {
  it("zeigt den Standard einer neuen Installation: an", async () => {
    mockHostsApi({ "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: true }] });
    renderCard();
    expect(await screen.findByRole("switch", { name: SWITCH })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("zeigt bei einer bestehenden Installation (aus) den Schalter ausgeschaltet", async () => {
    mockHostsApi({ "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: false }] });
    renderCard();
    expect(await screen.findByRole("switch", { name: SWITCH })).toHaveAttribute("aria-checked", "false");
  });

  it("ohne Wert in der Antwort gilt aus", async () => {
    mockHostsApi({ "GET /settings": [] });
    renderCard();
    expect(await screen.findByRole("switch", { name: SWITCH })).toHaveAttribute("aria-checked", "false");
  });

  it("speichert die Änderung", async () => {
    const calls = mockHostsApi({
      "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: false }],
      "PUT /settings/ssh.confirm_new_host_keys": { key: "ssh.confirm_new_host_keys", value: true },
    });
    renderCard();
    fireEvent.click(await screen.findByRole("switch", { name: SWITCH }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.path, c.body])).toEqual([
      ["/settings/ssh.confirm_new_host_keys", { value: true }],
    ]);
  });

  it("zeigt die Meldung des Servers, wenn die Umgebungsvariable das Ändern verbietet", async () => {
    mockHostsApi({
      "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: false }],
      "PUT /settings/ssh.confirm_new_host_keys": reply(409, { detail: "Das ist über die Umgebungsvariable NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS festgelegt und lässt sich hier nicht ändern." }),
    });
    renderCard();
    fireEvent.click(await screen.findByRole("switch", { name: SWITCH }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
  });

  it("gibt es ohne das Recht settings.write gar nicht", async () => {
    setUser(["hosts.read"]);
    const calls = mockHostsApi({});
    renderCard();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByText("Neue Server-Schlüssel")).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("meldet, wenn die Einstellung nicht geladen werden kann", async () => {
    mockHostsApi({ "GET /settings": reply(500, { detail: "kaputt" }) });
    renderCard();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Die Einstellung konnte nicht geladen werden."));
  });
});
