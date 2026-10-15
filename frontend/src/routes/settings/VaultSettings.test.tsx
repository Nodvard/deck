import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { mockHostsApi, reply, type Call } from "../../test/hostsMock";
import { VaultSettings, dateToIso, type VaultEntry } from "./VaultSettings";

function setUser(permissions: string[], isOwner = true) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "deck", display_name: "Deck", email: null, is_owner: isOwner, locale: "de", permissions },
  });
}

function entry(over: Partial<VaultEntry> = {}): VaultEntry {
  return {
    id: "s1", label: "backup-token", kind: "api_token", description: "für das Skript", created_at: "2026-09-01T10:00:00Z",
    rotated_at: null, last_used_at: null, expires_at: null, used_by: [], bound: [], ...over,
  };
}

const NETWORK = { type: "extension", id: "network", name: "Netzwerk", last_used_at: null };
const IN_USE = entry({ id: "s2", label: "network-npm-password", kind: "generic", description: "", used_by: [NETWORK], bound: [NETWORK] });
const DECK = { type: "login", id: "u1", name: "deck", last_used_at: null };
const LOGIN = entry({ id: "s3", label: "totp:deck", kind: "totp", description: "", used_by: [DECK], bound: [DECK] });

function api(routes: Record<string, unknown> = {}, totp = false): Call[] {
  return mockHostsApi({
    "GET /me": { totp_enabled: totp },
    "GET /secrets": [entry(), IN_USE, LOGIN],
    ...routes,
  });
}

beforeEach(() => setUser(["*"]));
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Seite „Tresor“", () => {
  it("zeigt Einträge mit Art, Verwendern und Ablauf, aber nie einen Wert", async () => {
    const soon = new Date(Date.now() + 5 * 24 * 3600 * 1000).toISOString();
    mockHostsApi({ "GET /me": { totp_enabled: false }, "GET /secrets": [entry({ expires_at: soon }), IN_USE] });
    render(<VaultSettings />);
    const row = within(await screen.findByTestId("vault-backup-token"));
    expect(row.getByText(/API-Token · für das Skript/)).toBeInTheDocument();
    expect(row.getByText("Nichts benutzt diesen Eintrag.")).toBeInTheDocument();
    expect(row.getByText(/läuft in 5 Tagen ab/)).toBeInTheDocument();
    const used = within(screen.getByTestId("vault-network-npm-password"));
    expect(used.getByText("Modul: Netzwerk")).toBeInTheDocument();
    expect(used.getByRole("button", { name: /Löschen/ })).toBeDisabled();
  });

  it("ersetzt einen Wert nur mit Passwort und fragt bei Zwei-Faktor nach dem Code", async () => {
    let attempts = 0;
    const calls = api({
      "PUT /secrets/s1/value": (call: Call) => {
        attempts += 1;
        const body = call.body as { totp_code?: string };
        return body.totp_code ? undefined : reply(403, { detail: "Bitte den Code aus der App eingeben.", code: "totp_missing" });
      },
    });
    render(<VaultSettings />);
    const row = within(await screen.findByTestId("vault-backup-token"));
    fireEvent.click(row.getByRole("button", { name: /Ersetzen/ }));
    const form = within(screen.getByTestId("vault-confirm"));
    const submit = form.getByRole("button", { name: "Ersetzen" });
    expect(submit).toBeDisabled();
    fireEvent.change(form.getByLabelText("Neuer Wert"), { target: { value: "NEU-123" } });
    fireEvent.change(form.getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(submit);

    expect(await screen.findByText("Bitte den Code aus der App eingeben.")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "123456" } });
    fireEvent.click(form.getByRole("button", { name: "Ersetzen" }));
    expect(await screen.findByText("„backup-token“ ersetzt.")).toBeInTheDocument();
    expect(attempts).toBe(2);
    const put = calls.filter((c) => c.method === "PUT").map((c) => c.body);
    expect(put).toEqual([
      { value: "NEU-123", current_password: "geheim" },
      { value: "NEU-123", current_password: "geheim", totp_code: "123456" },
    ]);
  });

  it("zeigt das Code-Feld gleich, wenn Zwei-Faktor an ist, und löscht mit Passwort und Code", async () => {
    const calls = api({ "DELETE /secrets/s1": undefined }, true);
    render(<VaultSettings />);
    const row = within(await screen.findByTestId("vault-backup-token"));
    fireEvent.click(row.getByRole("button", { name: /Löschen/ }));
    const form = within(await screen.findByTestId("vault-confirm"));
    await waitFor(() => expect(form.getByLabelText(/^Zwei-Faktor-Code/)).toBeInTheDocument());
    fireEvent.change(form.getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    expect(form.getByRole("button", { name: "Löschen" })).toBeDisabled();
    fireEvent.change(form.getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "654321" } });
    fireEvent.click(form.getByRole("button", { name: "Löschen" }));
    expect(await screen.findByText("„backup-token“ gelöscht.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ current_password: "geheim", totp_code: "654321" });
  });

  it("löscht einen Eintrag, den ein Modul nur gelesen hat, und zeigt die Nutzung als Hinweis", async () => {
    const read = { type: "extension", id: "proxmox", name: "Proxmox VE", last_used_at: "2026-10-14T08:00:00Z" };
    const calls = mockHostsApi({
      "GET /me": { totp_enabled: false },
      "GET /secrets": [entry({ used_by: [read] }), IN_USE],
      "DELETE /secrets/s1": undefined,
    });
    render(<VaultSettings />);
    const row = within(await screen.findByTestId("vault-backup-token"));
    expect(row.getByText("Modul: Proxmox VE (gelesen)")).toBeInTheDocument();
    expect(within(screen.getByTestId("vault-network-npm-password")).getByText("Modul: Netzwerk")).toBeInTheDocument();
    fireEvent.click(row.getByRole("button", { name: /Löschen/ }));
    const form = within(await screen.findByTestId("vault-confirm"));
    fireEvent.change(form.getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(form.getByRole("button", { name: "Löschen" }));
    expect(await screen.findByText("„backup-token“ gelöscht.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "DELETE")?.body).toEqual({ current_password: "geheim" });
  });

  it("ändert Beschreibung und Ablauf ohne Passwort", async () => {
    const calls = api({ "PATCH /secrets/s1": entry({ description: "neu" }) });
    render(<VaultSettings />);
    const row = within(await screen.findByTestId("vault-backup-token"));
    fireEvent.click(row.getByRole("button", { name: /Beschreibung und Ablauf/ }));
    const form = within(screen.getByTestId("vault-confirm"));
    fireEvent.change(form.getByLabelText("Beschreibung"), { target: { value: " neu " } });
    fireEvent.change(form.getByLabelText("Läuft ab am"), { target: { value: "2027-03-01" } });
    fireEvent.click(form.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("„backup-token“ gespeichert.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ description: "neu", expires_at: "2027-03-01T12:00:00Z" });
  });

  it("legt einen neuen Eintrag an", async () => {
    const calls = api({ "POST /secrets": entry({ id: "s9", label: "neu" }) });
    render(<VaultSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /Neuer Eintrag/ }));
    const form = within(screen.getByTestId("vault-new"));
    fireEvent.change(form.getByLabelText(/^Name/), { target: { value: " skript-token " } });
    fireEvent.change(form.getByLabelText("Art"), { target: { value: "api_token" } });
    fireEvent.change(form.getByLabelText("Wert"), { target: { value: "abc" } });
    fireEvent.click(form.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Eintrag gespeichert.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ label: "skript-token", kind: "api_token", value: "abc", description: "", expires_at: null });
  });

  it("bietet beim Zwei-Faktor-Schlüssel eines Kontos und ohne Schreibrecht nichts zum Ändern an", async () => {
    api();
    const { unmount } = render(<VaultSettings />);
    const login = within(await screen.findByTestId("vault-totp:deck"));
    expect(login.getByText(/Gehört zur Zwei-Faktor-Anmeldung/)).toBeInTheDocument();
    expect(login.queryByRole("button")).not.toBeInTheDocument();
    unmount();

    setUser(["secrets.read"], false);
    api();
    render(<VaultSettings />);
    await screen.findByTestId("vault-backup-token");
    expect(screen.queryByRole("button", { name: /Ersetzen|Löschen|Neuer Eintrag/ })).not.toBeInTheDocument();
  });

  it("rechnet ein Datum auf Mittag in UTC", () => {
    expect(dateToIso("2027-01-31")).toBe("2027-01-31T12:00:00Z");
    expect(dateToIso("")).toBeNull();
  });
});
