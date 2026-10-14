import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockHostsApi, reply } from "../../../test/hostsMock";
import { useAuthStore } from "../../../state/auth";
import { HostKeysCard } from "./HostKeysCard";

const SWITCH = "Neue Server-Schlüssel erst nach meiner Bestätigung merken";

function setUser(permissions: string[], isOwner = true) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: isOwner, locale: "de", permissions },
  });
}

async function confirmWith(password: string) {
  fireEvent.change(await screen.findByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Bestätigen und speichern" }));
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

  it("speichert die Änderung mit dem Passwort des Inhabers", async () => {
    const calls = mockHostsApi({
      "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: false }],
      "GET /me": { totp_enabled: false },
      "PUT /settings/ssh.confirm_new_host_keys": { key: "ssh.confirm_new_host_keys", value: true },
    });
    renderCard();
    fireEvent.click(await screen.findByRole("switch", { name: SWITCH }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await confirmWith("pw-inhaber");
    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.path, c.body])).toEqual([
      ["/settings/ssh.confirm_new_host_keys", { value: true, current_password: "pw-inhaber", totp_code: "" }],
    ]);
  });

  it("zeigt die Meldung des Servers, wenn die Umgebungsvariable das Ändern verbietet", async () => {
    mockHostsApi({
      "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: false }],
      "GET /me": { totp_enabled: false },
      "PUT /settings/ssh.confirm_new_host_keys": reply(409, { detail: "Das ist über die Umgebungsvariable NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS festgelegt und lässt sich hier nicht ändern." }),
    });
    renderCard();
    fireEvent.click(await screen.findByRole("switch", { name: SWITCH }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await confirmWith("pw-inhaber");
    expect(await screen.findByRole("alert")).toHaveTextContent("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS");
    // Die Bestätigung bleibt offen; ein neuer Versuch braucht das Passwort noch einmal.
    expect(screen.getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("");
  });

  it("andere als der Inhaber sehen den Schalter gesperrt und keinen Speichern-Knopf", async () => {
    setUser(["*"], false);
    const calls = mockHostsApi({ "GET /settings": [{ key: "ssh.confirm_new_host_keys", value: true }] });
    renderCard();
    expect(await screen.findByRole("switch", { name: SWITCH })).toBeDisabled();
    expect(screen.getByText(/Das ändert nur der Inhaber/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Speichern" })).toBeNull();
    expect(calls.filter((c) => c.method === "PUT")).toEqual([]);
  });

  it("gibt es ohne das Recht settings.write gar nicht", async () => {
    setUser(["hosts.read"], false);
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
