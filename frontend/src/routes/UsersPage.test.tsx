import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../components/GlobalDialogs";
import { useAuthStore } from "../state/auth";
import { UsersPage, roleLabel } from "./UsersPage";

const ROLES = [
  { id: "role-admin", name: "admin", description: "Eingebaute Rolle 'admin'", is_builtin: true },
  { id: "role-operator", name: "operator", description: "Eingebaute Rolle 'operator'", is_builtin: true },
  { id: "role-viewer", name: "viewer", description: "Eingebaute Rolle 'viewer'", is_builtin: true },
];
const USERS = [
  { id: "user-owner", username: "owner1", display_name: "", email: null, is_active: true, is_owner: true, roles: [] },
  {
    id: "user-nico", username: "nico", display_name: "Nico", email: null, is_active: true, is_owner: false,
    roles: [{ id: "role-viewer", name: "viewer", description: "", is_builtin: true }],
  },
];

function mockFetch(overrides: {
  users?: unknown[]; onCreate?: (body: unknown) => void; onPatch?: (id: string, body: unknown) => void; onDelete?: (id: string) => void;
  onReset?: (id: string) => void; meTotp?: boolean; answers?: Response[]; roles?: unknown[];
} = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/v1/me") && method === "GET" && overrides.meTotp !== undefined) {
      return new Response(JSON.stringify({ id: "user-owner", username: "owner1", totp_enabled: overrides.meTotp }), { status: 200 });
    }
    // Vorgegebene Antworten fuer Aenderungen (z. B. 403 totp_missing), der Reihe nach.
    if (method !== "GET" && overrides.answers?.length) return overrides.answers.shift()!;
    if (url.endsWith("/api/v1/users") && method === "GET") {
      return new Response(JSON.stringify(overrides.users ?? USERS), { status: 200 });
    }
    if (url.endsWith("/api/v1/roles") && method === "GET") {
      return new Response(JSON.stringify(overrides.roles ?? ROLES), { status: 200 });
    }
    if (url.endsWith("/api/v1/users") && method === "POST") {
      const body = JSON.parse(init?.body as string);
      overrides.onCreate?.(body);
      return new Response(JSON.stringify({ id: "user-new", ...body, is_active: true, is_owner: false, roles: [] }), { status: 201 });
    }
    const resetMatch = url.match(/\/api\/v1\/users\/([^/]+)\/reset-2fa$/);
    if (resetMatch && method === "POST") {
      overrides.onReset?.(resetMatch[1]);
      return new Response(null, { status: 204 });
    }
    const patchMatch = url.match(/\/api\/v1\/users\/([^/]+)$/);
    if (patchMatch && method === "PATCH") {
      const body = JSON.parse(init?.body as string);
      overrides.onPatch?.(patchMatch[1], body);
      return new Response(JSON.stringify({ ...USERS[1], ...body, roles: [] }), { status: 200 });
    }
    if (patchMatch && method === "DELETE") {
      overrides.onDelete?.(patchMatch[1]);
      return new Response(null, { status: 204 });
    }
    throw new Error(`Unerwarteter Fetch in diesem Test: ${method} ${url}`);
  });
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "user-owner", username: "owner1", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated", mfaToken: null,
  });
});

function renderPage() {
  return render(
    <MemoryRouter>
      <UsersPage />
      <GlobalDialogs />
    </MemoryRouter>,
  );
}

describe("UsersPage", () => {
  it("zeigt geladene Nutzer mit Rollen, Owner-Badge und ohne Loeschen-Knopf fuer sich selbst", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderPage();

    await screen.findByText("owner1");
    const ownerRow = within(screen.getByTestId("user-user-owner"));
    expect(ownerRow.getByText("Inhaber")).toBeInTheDocument();
    expect(ownerRow.queryByRole("button", { name: "Entfernen" })).not.toBeInTheDocument();

    const nicoRow = within(screen.getByTestId("user-user-nico"));
    expect(nicoRow.getByText("Betrachter")).toBeInTheDocument();
    expect(nicoRow.queryByText("viewer")).toBeNull();
    expect(nicoRow.getByRole("button", { name: "Entfernen" })).toBeInTheDocument();
  });

  it("die eingebauten Rollen stehen auf Deutsch da, eigene Rollen behalten ihren Namen", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderPage();
    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    expect(screen.getByLabelText("Administrator")).toBeInTheDocument();
    expect(screen.getByLabelText("Betrachter")).toBeInTheDocument();
    expect(screen.queryByLabelText("admin")).toBeNull();
    expect(roleLabel({ name: "operator", is_builtin: true })).toBe("Bediener");
    expect(roleLabel({ name: "operator", is_builtin: false })).toBe("operator");
    expect(roleLabel({ name: "buchhaltung", is_builtin: false })).toBe("buchhaltung");
  });

  it("sagt beim Bediener deutlich, dass er Befehle auf Servern ausfuehren kann (mit sudo-Regel: volle Rechte)", async () => {
    vi.stubGlobal("fetch", mockFetch());
    renderPage();
    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    const operator = screen.getByLabelText("Bediener").closest("label")!;
    expect(operator.textContent).toContain("Befehle ausführen");
    expect(operator.textContent).toContain("sudo-Regel");
    expect(operator.textContent).toContain("volle Rechte");
    expect(operator.textContent).toContain("Aktionen bis mittleres Risiko freigeben");
    // Der Betrachter führt nichts aus; sein Text spricht nicht von Befehlen.
    expect(screen.getByLabelText("Betrachter").closest("label")!.textContent).not.toContain("Befehle");
  });

  it("legt einen neuen Nutzer mit Rolle ueber das Formular an", async () => {
    let created: unknown = null;
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => (created = body) }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "Frisch" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByLabelText("Administrator"));
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await waitFor(() => expect(created).not.toBeNull());
    expect((created as { username: string }).username).toBe("frisch");
    expect((created as { role_ids: string[] }).role_ids).toEqual(["role-admin"]);
  });

  it.each([
    ["Kollege Max", "correct-horse-battery", /^Benutzername: Nur Kleinbuchstaben, Ziffern sowie \. - und _ erlaubt, ohne Leerzeichen/],
    ["ko", "correct-horse-battery", "Benutzername: Mindestens 3 Zeichen."],
    ["frisch", "kurz", "Passwort: Mindestens 8 Zeichen."],
  ])("lehnt %j / Passwort mit klarer Meldung ab, ohne etwas zu senden", async (username, password, expected) => {
    let created: unknown = null;
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => (created = body) }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    expect(screen.getByText(/Nur Kleinbuchstaben, Ziffern sowie \. - und _, mindestens 3 Zeichen, ohne Leerzeichen/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: username } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: password } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(created).toBeNull();
  });

  it("lehnt eine E-Mail ohne @ ab, ohne etwas zu senden (das Formular hat noValidate, der Browser prüft sie nicht)", async () => {
    let created: unknown = null;
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => (created = body) }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "frisch" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText(/^E-Mail/), { target: { value: "kein-email" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    expect(await screen.findByText(/^E-Mail: Das sieht nicht nach einer Adresse aus/)).toBeInTheDocument();
    expect(created).toBeNull();
  });

  it("sendet eine gültige E-Mail ohne Leerzeichen am Rand, eine leere als null", async () => {
    const created: { email: string | null }[] = [];
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => created.push(body as { email: string | null }) }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "frisch" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText(/^E-Mail/), { target: { value: " frisch@beispiel.de " } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0].email).toBe("frisch@beispiel.de");

    await screen.findByText(/Benutzer „frisch“ angelegt/);
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "zweiter" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(created).toHaveLength(2));
    expect(created[1].email).toBeNull();
  });

  it("Bearbeiten: eine geänderte, kaputte E-Mail wird abgelehnt; eine alte, unveränderte blockiert das Speichern nicht", async () => {
    const onPatch = vi.fn();
    const users = [USERS[0], { ...USERS[1], email: "alt-ohne-at " }];
    vi.stubGlobal("fetch", mockFetch({ users, onPatch }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: "Bearbeiten" }));

    // Nur den Anzeigenamen ändern: die alte Adresse wird nicht erneut geprüft und nicht mitgeschickt.
    fireEvent.change(screen.getByLabelText(/^Anzeigename/), { target: { value: "Nico B." } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(onPatch).toHaveBeenCalledWith("user-nico", { display_name: "Nico B." }));

    // Neue, kaputte Adresse: Meldung, kein zweiter Aufruf.
    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: "Bearbeiten" }));
    fireEvent.change(screen.getByLabelText(/^E-Mail/), { target: { value: "kaputt" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText(/^E-Mail: Das sieht nicht nach einer Adresse aus/)).toBeInTheDocument();
    expect(onPatch).toHaveBeenCalledTimes(1);

    // Gültige Adresse: wird getrimmt gesendet.
    fireEvent.change(screen.getByLabelText(/^E-Mail/), { target: { value: " nico@beispiel.de " } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(2));
    expect(onPatch).toHaveBeenLastCalledWith("user-nico", { email: "nico@beispiel.de" });
  });

  it("zeigt die Ablehnung des Servers (422) als Satz, nicht als „HTTP 422“", async () => {
    const msg = "Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt, ohne Leerzeichen; er muss mit einem Buchstaben oder einer Ziffer beginnen.";
    const base = mockFetch();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/users") && init?.method === "POST") {
        return new Response(JSON.stringify({ detail: [{ type: "value_error", loc: ["body", "username"], msg }] }), { status: 422 });
      }
      return base(input, init);
    }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "frisch" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByText(msg)).toBeInTheDocument();
  });

  it("aendert die Rollen eines Nutzers", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByText("owner1");
    const nicoRow = within(screen.getByTestId("user-user-nico"));
    fireEvent.click(nicoRow.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByLabelText("Administrator"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/users/user-nico"),
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ role_ids: ["role-viewer", "role-admin"] }) }),
      ),
    );
  });

  it("deaktiviert einen Nutzer, aber zeigt keinen Deaktivieren-Knopf fuer den Owner", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByText("owner1");
    const ownerRow = within(screen.getByTestId("user-user-owner"));
    expect(ownerRow.queryByRole("button", { name: "Deaktivieren" })).not.toBeInTheDocument();

    const nicoRow = within(screen.getByTestId("user-user-nico"));
    fireEvent.click(nicoRow.getByRole("button", { name: "Deaktivieren" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/users/user-nico"),
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ is_active: false }) }),
      ),
    );
  });

  it("fragt vor dem Entfernen eines Nutzers ueber den echten Dialog nach", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByText("owner1");
    const nicoRow = within(screen.getByTestId("user-user-nico"));
    fireEvent.click(nicoRow.getByRole("button", { name: "Entfernen" }));

    await screen.findByText('"nico" wirklich entfernen?');
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Entfernen" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/users/user-nico"), expect.objectContaining({ method: "DELETE" })),
    );
  });

  it("Zwei-Faktor zurücksetzen: nur bei anderen mit aktivem 2FA, nie beim Inhaber, mit eigenem Passwort", async () => {
    const onReset = vi.fn();
    const users = [
      { ...USERS[0], totp_enabled: true },
      { ...USERS[1], totp_enabled: true },
      { id: "user-ohne", username: "ohne", display_name: "", email: null, is_active: true, is_owner: false, roles: [], totp_enabled: false },
    ];
    const fetchMock = mockFetch({ users, onReset });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByText("owner1");
    expect(within(screen.getByTestId("user-user-owner")).queryByRole("button", { name: "Zwei-Faktor zurücksetzen" })).not.toBeInTheDocument();
    expect(within(screen.getByTestId("user-user-ohne")).queryByRole("button", { name: "Zwei-Faktor zurücksetzen" })).not.toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: "Zwei-Faktor zurücksetzen" }));
    expect(await screen.findByText(/überall abgemeldet/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "Zurücksetzen" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Dein eigenes Passwort/), { target: { value: "admin-passwort" } });
    fireEvent.click(confirm);

    await waitFor(() => expect(onReset).toHaveBeenCalledWith("user-nico"));
    const post = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/reset-2fa"))!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({ current_password: "admin-passwort" });
    await screen.findByText(/Zwei-Faktor-Anmeldung von „nico“ zurückgesetzt/);
  });

  it("Passwortfeld beim Bearbeiten: nicht beim eigenen Konto, nicht beim Inhaber, bei anderen schon", async () => {
    // Angemeldet als nico (kein Inhaber); owner1 ist der Inhaber.
    useAuthStore.setState({
      accessToken: "tok",
      user: { id: "user-nico", username: "nico", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["users.read", "users.write"] },
      status: "authenticated", mfaToken: null,
    });
    const users = [...USERS, { id: "user-x", username: "xaver", display_name: "", email: null, is_active: true, is_owner: false, roles: [] }];
    vi.stubGlobal("fetch", mockFetch({ users }));
    renderPage();
    await screen.findByText("owner1");

    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: /Bearbeiten/ }));
    expect(screen.queryByLabelText(/Neues Passwort setzen/)).not.toBeInTheDocument();
    expect(screen.getByText(/unter „Mein Konto“/)).toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId("user-user-owner")).getByRole("button", { name: /Bearbeiten/ }));
    expect(screen.queryByLabelText(/Neues Passwort setzen/)).not.toBeInTheDocument();
    expect(screen.getByText(/nur der Inhaber selbst/)).toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId("user-user-x")).getByRole("button", { name: /Bearbeiten/ }));
    expect(screen.getByLabelText(/Neues Passwort setzen/)).toBeInTheDocument();
  });

  it("Zwei-Faktor zurücksetzen mit eigener Zwei-Faktor-Anmeldung: auch der eigene Code geht mit", async () => {
    const onReset = vi.fn();
    const users = [{ ...USERS[0], totp_enabled: true }, { ...USERS[1], totp_enabled: true }];
    const fetchMock = mockFetch({ users, onReset, meTotp: true });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("owner1");

    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: "Zwei-Faktor zurücksetzen" }));
    const code = await screen.findByLabelText(/^Zwei-Faktor-Code/);
    const confirm = screen.getByRole("button", { name: "Zurücksetzen" });
    fireEvent.change(screen.getByLabelText(/^Dein eigenes Passwort/), { target: { value: "admin-passwort" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(code, { target: { value: " 123456 " } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(onReset).toHaveBeenCalledWith("user-nico"));
    const post = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/reset-2fa"))!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({ current_password: "admin-passwort", totp_code: "123456" });
  });

  it("Zwei-Faktor zurücksetzen: fragt der Server nach dem Code, erscheint das Feld", async () => {
    const users = [{ ...USERS[0], totp_enabled: true }, { ...USERS[1], totp_enabled: true }];
    const missing = new Response(JSON.stringify({ detail: "Bitte gib zusätzlich den aktuellen Zwei-Faktor-Code ein.", code: "totp_missing" }), { status: 403 });
    vi.stubGlobal("fetch", mockFetch({ users, answers: [missing] }));
    renderPage();
    await screen.findByText("owner1");

    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: "Zwei-Faktor zurücksetzen" }));
    await screen.findByText(/überall abgemeldet/);
    expect(screen.queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Dein eigenes Passwort/), { target: { value: "admin-passwort" } });
    fireEvent.click(screen.getByRole("button", { name: "Zurücksetzen" }));

    expect(await screen.findByLabelText(/^Zwei-Faktor-Code/)).toBeInTheDocument();
    expect(screen.getByText("Gib zusätzlich deinen eigenen Zwei-Faktor-Code ein.")).toBeInTheDocument();
  });

  it("Fremdes Passwort setzen: verlangt das eigene Passwort und, bei eigener Zwei-Faktor-Anmeldung, den Code", async () => {
    const onPatch = vi.fn();
    const fetchMock = mockFetch({ onPatch, meTotp: true });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("owner1");

    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: /Bearbeiten/ }));
    // Ohne neues Passwort keine Bestätigung nötig.
    expect(screen.queryByLabelText(/^Dein eigenes Passwort/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Neues Passwort setzen/), { target: { value: "neues-passwort-1" } });
    const save = screen.getByRole("button", { name: "Speichern" });
    expect(screen.getByText(/Unter „Meldungen“ steht danach/)).toBeInTheDocument();
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Dein eigenes Passwort/), { target: { value: "admin-passwort" } });
    expect(save).toBeDisabled();
    fireEvent.change(await screen.findByLabelText(/^Zwei-Faktor-Code/), { target: { value: "654321" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(onPatch).toHaveBeenCalled());
    expect(onPatch.mock.calls[0][1]).toEqual({ password: "neues-passwort-1", current_password: "admin-passwort", totp_code: "654321" });
  });

  const ROLES_WITH_ADMIN_FLAG = [{ ...ROLES[0], manages_users: true }, { ...ROLES[1], manages_users: false }];

  it("Neues Konto mit Verwaltungsrechten: verlangt das eigene Passwort und schickt es mit", async () => {
    let created: unknown = null;
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => (created = body), roles: ROLES_WITH_ADMIN_FLAG }));
    renderPage();
    await screen.findByText("owner1");

    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "frisch" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    expect(screen.queryByLabelText(/^Dein eigenes Passwort/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Administrator"));
    expect(screen.getByText(/Diese Rolle darf andere Konten verwalten/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByText("Zur Bestätigung brauchst du dein eigenes Passwort.")).toBeInTheDocument();
    expect(created).toBeNull();

    fireEvent.change(screen.getByLabelText(/^Dein eigenes Passwort/), { target: { value: "admin-passwort" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(created).not.toBeNull());
    expect(created).toMatchObject({ role_ids: ["role-admin"], current_password: "admin-passwort" });
  });

  it("Verwaltungsrolle vergeben: verlangt beim Bearbeiten das eigene Passwort", async () => {
    const onPatch = vi.fn();
    vi.stubGlobal("fetch", mockFetch({ onPatch, roles: ROLES_WITH_ADMIN_FLAG }));
    renderPage();
    await screen.findByText("owner1");

    fireEvent.click(within(screen.getByTestId("user-user-nico")).getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByLabelText("Administrator"));
    const save = screen.getByRole("button", { name: "Speichern" });
    expect(screen.getByText(/dass du sie „nico“ gegeben hast/)).toBeInTheDocument();
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Dein eigenes Passwort/), { target: { value: "admin-passwort" } });
    fireEvent.click(save);

    await waitFor(() => expect(onPatch).toHaveBeenCalled());
    expect(onPatch.mock.calls[0][1]).toEqual({ role_ids: ["role-viewer", "role-admin"], current_password: "admin-passwort" });
  });
});
