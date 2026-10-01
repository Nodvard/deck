import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { useAuthStore } from "../../state/auth";
import { ExtensionConfigPage } from "./ExtensionConfigPage";
import { ExtensionsSettings } from "./ExtensionsSettings";

type Call = { method: string; url: string; body: unknown };
type Handler = (body: unknown, url: string) => unknown;

/** Antwortet nach "METHODE /pfad" (ohne Query); `Response`-Objekte gehen unverändert durch. */
function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = (typeof input === "string" ? input : input.toString()).replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body });
    const handler = routes[`${method} ${url.split("?")[0]}`];
    if (!handler) throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
    const result = handler(body, url);
    if (result instanceof Response) return result;
    return result === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(result), { status: 200 });
  }));
  return calls;
}

const fail = (status: number, detail: string) => new Response(JSON.stringify({ detail }), { status });

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});

const SCHEMA = {
  type: "object",
  properties: {
    server_url: { type: "string", title: "Server", pattern: "^https?://\\S+$", "x-pattern-message": "Die Adresse muss mit http:// beginnen." },
    topic: { type: "string", title: "Thema" },
  },
  required: ["server_url", "topic"],
  "x-test-message": true,
  "x-secrets": [
    { label: "tok", title: "Zugriffstoken (optional)", optional: true },
    { label: "pw", title: "Passwort" },
  ],
};

function ext(extra: object = {}) {
  return { id: "demo", name: "Demo", description: null, icon: "server", state: "enabled", version: "1", ...extra };
}

function settings(secretState: Record<string, boolean> = {}, values: object = { server_url: "https://x", topic: "t" }) {
  return {
    schema: SCHEMA, values,
    secrets: [
      { label: "tok", title: "Zugriffstoken (optional)", description: null, item: null, is_set: secretState.tok ?? false, optional: true },
      { label: "pw", title: "Passwort", description: null, item: null, is_set: secretState.pw ?? false },
    ],
  };
}

function renderConfig() {
  return render(
    <MemoryRouter initialEntries={["/settings/extensions/demo"]}>
      <Routes><Route path="/settings/extensions/:extId" element={<><ExtensionConfigPage /><GlobalDialogs /></>} /></Routes>
    </MemoryRouter>,
  );
}

describe("Verbindung testen", () => {
  it("zeigt ein erfolgreiches Ergebnis grün und fragt den Test-Endpunkt", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "POST /extensions/demo/test": () => ({ ok: true, message: "Verbindung funktioniert.", details: null }),
    });
    renderConfig();
    fireEvent.click(await screen.findByRole("button", { name: "Verbindung testen" }));
    const box = await screen.findByTestId("test-result");
    expect(within(box).getByText("Verbindung funktioniert.")).toBeInTheDocument();
    expect(box.className).toContain("emerald");
    expect(calls.find((c) => c.url === "/extensions/demo/test")?.body).toEqual({ mode: "connection" });
  });

  it("zeigt einen Fehler rot, samt Ergebnis je Verbindung", async () => {
    mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "POST /extensions/demo/test": () => ({
        ok: false, message: "1 von 2 Verbindungen funktionieren nicht – Einzelheiten unten.",
        details: [
          { name: "pve1", ok: true, message: "Verbindung funktioniert." },
          { name: "pve2", ok: false, message: "Keine Antwort von 10.0.0.2:8006 – Adresse und Port prüfen." },
        ],
      }),
    });
    renderConfig();
    fireEvent.click(await screen.findByRole("button", { name: "Verbindung testen" }));
    const box = await screen.findByTestId("test-result");
    expect(box.className).toContain("red");
    expect(within(box).getByText(/1 von 2 Verbindungen/)).toBeInTheDocument();
    expect(within(box).getByText(/Keine Antwort von 10.0.0.2:8006/)).toBeInTheDocument();
    expect(within(box).getByText(/pve1:/)).toBeInTheDocument();
  });

  it("zeigt Fehler des Servers (z. B. Begrenzung) als rotes Ergebnis", async () => {
    mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "POST /extensions/demo/test": () => fail(429, "Zu viele Tests hintereinander. Bitte in 1 Minute erneut versuchen."),
    });
    renderConfig();
    fireEvent.click(await screen.findByRole("button", { name: "Verbindung testen" }));
    const box = await screen.findByTestId("test-result");
    expect(box.className).toContain("red");
    expect(within(box).getByText(/Zu viele Tests/)).toBeInTheDocument();
  });

  it("sendet mit „Testnachricht senden“ den Modus message", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "POST /extensions/demo/test": () => ({ ok: true, message: "Testnachricht gesendet – sie sollte gleich auf dem Gerät ankommen." }),
    });
    renderConfig();
    fireEvent.click(await screen.findByRole("button", { name: "Testnachricht senden" }));
    await screen.findByText(/Testnachricht gesendet/);
    expect(calls.find((c) => c.url === "/extensions/demo/test")?.body).toEqual({ mode: "message" });
  });

  it("verlangt erst Speichern, wenn das Formular geändert ist, und erst Einschalten bei ausgeschalteten Modulen", async () => {
    mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
    });
    renderConfig();
    const button = await screen.findByRole("button", { name: "Verbindung testen" });
    expect(button).toBeEnabled();
    fireEvent.change(screen.getByLabelText(/^Thema/), { target: { value: "neu" } });
    expect(button).toBeDisabled();
    expect(screen.getByText(/Erst speichern, dann testen/)).toBeInTheDocument();
  });

  it("deaktiviert den Test bei ausgeschalteter Erweiterung", async () => {
    mockApi({
      "GET /extensions/demo": () => ext({ state: "disabled" }),
      "GET /extensions/demo/settings": () => settings(),
    });
    renderConfig();
    await screen.findByText(/Erst die Erweiterung einschalten/);
    expect(screen.getByRole("button", { name: "Verbindung testen" })).toBeDisabled();
  });

  it("zeigt den letzten Test, wenn die Seite neu geöffnet wird", async () => {
    mockApi({
      "GET /extensions/demo": () => ext({ last_test: { ok: false, message: "Zugangsdaten abgelehnt – Benutzername, Token oder Passwort prüfen." } }),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
    });
    renderConfig();
    const box = await screen.findByTestId("test-result");
    expect(box).toHaveTextContent("Letzter Test:");
    expect(box).toHaveTextContent("Zugangsdaten abgelehnt");
  });
});

describe("Einrichtung nötig", () => {
  it("zeigt auf der Einstellungsseite die Gründe", async () => {
    mockApi({
      "GET /extensions/demo": () => ext({ needs_setup: true, setup_reasons: ["„Thema“ ist noch nicht ausgefüllt.", "Zugangsdaten fehlen: Passwort."] }),
      "GET /extensions/demo/settings": () => settings(),
    });
    renderConfig();
    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("Einrichtung nötig");
    expect(banner).toHaveTextContent("Zugangsdaten fehlen: Passwort.");
  });

  it("markiert in der Modul-Liste Module, die Einrichtung brauchen, mit Link zur Einstellungsseite", async () => {
    mockApi({
      "GET /extensions": () => [
        { id: "proxmox", version: "1", state: "enabled", source: "bundled", name: "Proxmox VE", description: null, icon: "server", granted_permissions: [], last_error: null, has_settings: true, needs_setup: true, setup_reasons: ["Zugangsdaten fehlen: API-Token-Geheimnis (pve1).", "Bei „Proxmox-Server“ fehlt noch ein Eintrag.", "Dritter Grund."] },
        { id: "ntfy", version: "1", state: "enabled", source: "bundled", name: "ntfy", description: null, icon: "bell", granted_permissions: [], last_error: null, has_settings: true, needs_setup: false, setup_reasons: [] },
      ],
    });
    render(
      <QueryClientProvider client={new QueryClient()}><MemoryRouter><ExtensionsSettings /></MemoryRouter></QueryClientProvider>,
    );
    const row = await screen.findByTestId("ext-proxmox");
    expect(within(row).getByText("Einrichtung nötig")).toBeInTheDocument();
    expect(within(row).getByText("Zugangsdaten fehlen: API-Token-Geheimnis (pve1).")).toBeInTheDocument();
    expect(within(row).getByText("und 1 weitere")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: /Jetzt einrichten/ })).toHaveAttribute("href", "/settings/extensions/proxmox");
    const ok = screen.getByTestId("ext-ntfy");
    expect(within(ok).queryByText("Einrichtung nötig")).toBeNull();
    expect(within(ok).getByRole("link", { name: /Konfigurieren/ })).toBeInTheDocument();
  });
});

describe("Zugangsdaten in der Verbindungskarte", () => {
  it("zeigt gesetzt / nicht gesetzt / optional, nie einen Wert", async () => {
    mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
    });
    renderConfig();
    const pw = await screen.findByTestId("secret-pw");
    expect(within(pw).getByText("Hinterlegt")).toBeInTheDocument();
    expect(within(pw).getByRole("button", { name: "Ersetzen" })).toBeInTheDocument();
    expect(within(pw).getByRole("button", { name: "Entfernen" })).toBeInTheDocument();
    expect(within(pw).queryByLabelText("Passwort")).toBeNull(); // kein Eingabefeld, solange nichts ersetzt wird
    const tok = screen.getByTestId("secret-tok");
    expect(within(tok).getByText("Nicht gesetzt (optional)")).toBeInTheDocument();
    expect(within(tok).queryByRole("button", { name: "Entfernen" })).toBeNull();
  });

  it("ersetzt ein Geheimnis über das Eingabefeld", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "PUT /extensions/demo/secrets": () => undefined,
    });
    renderConfig();
    const pw = await screen.findByTestId("secret-pw");
    fireEvent.click(within(pw).getByRole("button", { name: "Ersetzen" }));
    fireEvent.change(within(pw).getByLabelText("Passwort"), { target: { value: "neu-geheim" } });
    fireEvent.click(within(pw).getByRole("button", { name: "Ersetzen" }));
    await within(pw).findByText("Ersetzt.");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ label: "pw", value: "neu-geheim" });
    expect(within(pw).getByText("Hinterlegt")).toBeInTheDocument();
    expect(within(pw).queryByLabelText("Passwort")).toBeNull();
  });

  it("entfernt ein Geheimnis erst nach Rückfrage", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "DELETE /extensions/demo/secrets": () => undefined,
    });
    renderConfig();
    const pw = await screen.findByTestId("secret-pw");
    fireEvent.click(within(pw).getByRole("button", { name: "Entfernen" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("„Passwort“ wirklich entfernen?");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Entfernen" }));
    await within(pw).findByText("Entfernt.");
    expect(calls.find((c) => c.method === "DELETE")?.url).toBe("/extensions/demo/secrets?label=pw");
    expect(within(pw).getByText("Fehlt")).toBeInTheDocument();
    expect(within(pw).getByLabelText("Passwort")).toBeInTheDocument(); // gleich neu eintragbar
  });

  it("entfernt nichts, wenn die Rückfrage abgebrochen wird", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
    });
    renderConfig();
    const pw = await screen.findByTestId("secret-pw");
    fireEvent.click(within(pw).getByRole("button", { name: "Entfernen" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(within(pw).getByText("Hinterlegt")).toBeInTheDocument();
  });
});

describe("Musterprüfung (pattern)", () => {
  it("zeigt die deutsche Meldung und sperrt das Speichern, bis das Muster passt", async () => {
    const calls = mockApi({
      "GET /extensions/demo": () => ext(),
      "GET /extensions/demo/settings": () => settings({ pw: true }),
      "PUT /extensions/demo/settings": (body) => settings({ pw: true }, (body as { values: object }).values),
    });
    renderConfig();
    const field = await screen.findByLabelText(/^Server/);
    fireEvent.change(field, { target: { value: "nas.lan" } });
    expect(await screen.findAllByText("Die Adresse muss mit http:// beginnen.")).not.toHaveLength(0);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(screen.getAllByRole("button", { name: "Speichern" })[0]).toBeDisabled();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    fireEvent.change(field, { target: { value: "http://nas.lan" } });
    expect(screen.queryByText("Die Adresse muss mit http:// beginnen.")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Speichern" })[0]);
    await screen.findByText("Einstellungen gespeichert.");
  });
});
