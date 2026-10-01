import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../components/GlobalDialogs";
import { useAuthStore } from "../state/auth";
import { UsersPage } from "./UsersPage";

const ROLES = [
  { id: "role-admin", name: "admin", description: "Eingebaute Rolle 'admin'", is_builtin: true },
  { id: "role-viewer", name: "viewer", description: "Eingebaute Rolle 'viewer'", is_builtin: true },
];
const USERS = [
  { id: "user-owner", username: "owner1", display_name: "", email: null, is_active: true, is_owner: true, roles: [] },
  {
    id: "user-nico", username: "nico", display_name: "Nico", email: null, is_active: true, is_owner: false,
    roles: [{ id: "role-viewer", name: "viewer", description: "", is_builtin: true }],
  },
];

function mockFetch(overrides: { users?: unknown[]; onCreate?: (body: unknown) => void; onPatch?: (id: string, body: unknown) => void; onDelete?: (id: string) => void; onReset?: (id: string) => void } = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    if (url.endsWith("/api/v1/users") && method === "GET") {
      return new Response(JSON.stringify(overrides.users ?? USERS), { status: 200 });
    }
    if (url.endsWith("/api/v1/roles") && method === "GET") {
      return new Response(JSON.stringify(ROLES), { status: 200 });
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
    expect(nicoRow.getByText("viewer")).toBeInTheDocument();
    expect(nicoRow.getByRole("button", { name: "Entfernen" })).toBeInTheDocument();
  });

  it("legt einen neuen Nutzer mit Rolle ueber das Formular an", async () => {
    let created: unknown = null;
    vi.stubGlobal("fetch", mockFetch({ onCreate: (body) => (created = body) }));
    renderPage();

    await screen.findByText("owner1");
    fireEvent.click(screen.getByRole("button", { name: "Neuer Benutzer" }));
    fireEvent.change(screen.getByLabelText(/^Benutzername/), { target: { value: "Frisch" } });
    fireEvent.change(screen.getByLabelText(/^Passwort/), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByLabelText("admin"));
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

    await waitFor(() => expect(created).not.toBeNull());
    expect((created as { username: string }).username).toBe("frisch");
    expect((created as { role_ids: string[] }).role_ids).toEqual(["role-admin"]);
  });

  it("aendert die Rollen eines Nutzers", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByText("owner1");
    const nicoRow = within(screen.getByTestId("user-user-nico"));
    fireEvent.click(nicoRow.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByLabelText("admin"));
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
});
