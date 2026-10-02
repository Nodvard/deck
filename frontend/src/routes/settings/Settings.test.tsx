import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { browserTimeZone, getDeckTimezone, setDeckTimezone } from "../../lib/deckTimezone";
import { useAuthStore } from "../../state/auth";
import { useBrandingStore } from "../../state/branding";
import { AccountSettings } from "./AccountSettings";
import { AppearanceSettings } from "./AppearanceSettings";
import { AuditSettings, targetText } from "./AuditSettings";
import { AutomationSettings } from "./AutomationSettings";
import { ExtensionConfigPage } from "./ExtensionConfigPage";
import { ExtensionsSettings } from "./ExtensionsSettings";
import { SettingsLayout } from "./SettingsLayout";
import { SystemSettings } from "./SystemSettings";
import { filterTimezones, timezoneOptions } from "./TimezonePicker";

const BRANDING = {
  product_name: "Alt-Name", short_name: "Alt", logo_url: null as string | null, favicon_url: null,
  login_subtitle: null, support_url: null,
  colors: { accent: "#0ea5e9", accent_strong: "#0369a1", background: "#0b1220", surface: "#111a2b", text: "#e6ebf5" },
};

type Handler = (method: string, body: unknown) => unknown;

/** Mein Konto: nach „Einrichten“ will der Server das aktuelle Passwort. */
async function enterSetupPassword(password = "geheim123") {
  fireEvent.change(await screen.findByLabelText(/^Passwort zur Bestätigung/), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
}

function mockApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ method, path, body });
    const handler = routes[`${method} ${path}`];
    if (!handler) throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
    const result = handler(method, body);
    return result === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(result), { status: 200 });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

function setUser(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
  });
}

beforeEach(() => {
  setUser(["*"]);
  useBrandingStore.setState({ branding: structuredClone(BRANDING), loaded: true });
});

describe("Einstellungen", () => {
  it("zeigt nur die Bereiche, für die Rechte vorhanden sind", () => {
    setUser(["audit.read"]);
    render(
      <MemoryRouter initialEntries={["/settings/account"]}>
        <Routes><Route path="/settings/*" element={<SettingsLayout />} /></Routes>
      </MemoryRouter>,
    );
    const nav = within(screen.getByRole("navigation", { name: "Einstellungen" }));
    expect(nav.getByText("Mein Konto")).toBeInTheDocument();
    expect(nav.getByText("Protokoll")).toBeInTheDocument();
    expect(nav.queryByText("Benutzer")).not.toBeInTheDocument();
    expect(nav.queryByText("Aussehen")).not.toBeInTheDocument();
    expect(nav.queryByText("System")).not.toBeInTheDocument();
  });

  it("zeigt den Bereich „System“ mit der Berechtigung für die Einstellungen", () => {
    setUser(["settings.write"]);
    render(
      <MemoryRouter initialEntries={["/settings/account"]}>
        <Routes><Route path="/settings/*" element={<SettingsLayout />} /></Routes>
      </MemoryRouter>,
    );
    const nav = within(screen.getByRole("navigation", { name: "Einstellungen" }));
    expect(nav.getByRole("link", { name: /System/ })).toHaveAttribute("href", "/settings/system");
  });

  it("„Server & Zugänge“ gibt es nur mit dem Recht hosts.write", () => {
    setUser(["hosts.read", "audit.read"]);
    render(
      <MemoryRouter initialEntries={["/settings/account"]}>
        <Routes><Route path="/settings/*" element={<SettingsLayout />} /></Routes>
      </MemoryRouter>,
    );
    const nav = within(screen.getByRole("navigation", { name: "Einstellungen" }));
    expect(nav.queryByText("Server & Zugänge")).not.toBeInTheDocument();
  });

  it("„Server & Zugänge“ steht mit hosts.write in der Navigation, nach „Benutzer“", () => {
    setUser(["hosts.write", "users.read"]);
    render(
      <MemoryRouter initialEntries={["/settings/account"]}>
        <Routes><Route path="/settings/*" element={<SettingsLayout />} /></Routes>
      </MemoryRouter>,
    );
    const nav = within(screen.getByRole("navigation", { name: "Einstellungen" }));
    const link = nav.getByText("Server & Zugänge").closest("a");
    expect(link).toHaveAttribute("href", "/settings/hosts");
    const labels = nav.getAllByRole("link").map((a) => a.textContent ?? "");
    expect(labels.findIndex((t) => t.startsWith("Server & Zugänge"))).toBe(labels.findIndex((t) => t.startsWith("Benutzer")) + 1);
  });

  it("Mein Konto: Profil speichern und Passwort mit Prüfung ändern", async () => {
    const me = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["*"], totp_enabled: false };
    const calls = mockApi({
      "GET /me": () => me,
      "PATCH /me": (_m, body) => ({ ...me, ...(body as object) }),
      "POST /me/password": () => undefined,
    });
    render(<AccountSettings />);
    fireEvent.change(await screen.findByDisplayValue("Nico"), { target: { value: "Nico B." } });
    fireEvent.click(screen.getByRole("button", { name: "Profil speichern" }));
    await screen.findByText("Profil gespeichert.");
    expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ display_name: "Nico B." });
    expect(useAuthStore.getState().user?.display_name).toBe("Nico B.");

    fireEvent.change(screen.getByLabelText("Aktuelles Passwort"), { target: { value: "alt-passwort" } });
    fireEvent.change(screen.getByLabelText("Neues Passwort"), { target: { value: "neues-passwort" } });
    fireEvent.change(screen.getByLabelText("Neues Passwort wiederholen"), { target: { value: "vertippt" } });
    fireEvent.click(screen.getByRole("button", { name: "Passwort ändern" }));
    await screen.findByText("Die beiden neuen Passwörter stimmen nicht überein.");
    expect(calls.some((c) => c.path === "/me/password")).toBe(false);

    fireEvent.change(screen.getByLabelText("Neues Passwort wiederholen"), { target: { value: "neues-passwort" } });
    fireEvent.click(screen.getByRole("button", { name: "Passwort ändern" }));
    await screen.findByText("Passwort geändert.");
    expect(calls.find((c) => c.path === "/me/password")?.body).toEqual({ current_password: "alt-passwort", new_password: "neues-passwort" });
  });

  it("Mein Konto: ausgeblendete Erste Schritte lassen sich wieder anzeigen", async () => {
    const me = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["*"], totp_enabled: false };
    const calls = mockApi({
      "GET /me": () => me,
      "GET /me/preferences": () => ({ first_steps_dismissed: true }),
      "PATCH /me/preferences": (_m, body) => body,
    });
    render(<AccountSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Wieder anzeigen" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Wieder anzeigen" })).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "PATCH" && c.path === "/me/preferences")?.body).toEqual({ first_steps_dismissed: false });
  });

  it("Mein Konto: ohne ausgeblendete Erste Schritte keine Karte dazu", async () => {
    const me = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["*"], totp_enabled: false };
    mockApi({ "GET /me": () => me, "GET /me/preferences": () => ({ first_steps_dismissed: false }) });
    render(<AccountSettings />);
    await screen.findByDisplayValue("Nico");
    expect(screen.queryByText("Wieder anzeigen")).not.toBeInTheDocument();
  });

  it("Mein Konto: Zwei-Faktor einrichten zeigt den Schlüssel und bestätigt den Code", async () => {
    let enabled = false;
    const calls = mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", display_name: "", email: null, is_owner: false, locale: "de", permissions: [], totp_enabled: enabled }),
      "POST /me/totp/setup": () => ({ secret: "ABCDEFGHIJKLMNOP", otpauth_uri: "otpauth://totp/x?secret=ABCDEFGHIJKLMNOP" }),
      "POST /me/totp/confirm": () => { enabled = true; return undefined; },
    });
    render(<AccountSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Einrichten" }));
    await enterSetupPassword();
    expect(await screen.findByTestId("totp-secret")).toHaveTextContent("ABCD EFGH IJKL MNOP");
    // Der QR-Code kommt aus dem otpauth-Link des Servers und wird im Browser gezeichnet.
    const qr = screen.getByRole("img", { name: "QR-Code für die Authenticator-App" });
    expect(qr.tagName.toLowerCase()).toBe("svg");
    expect(qr.querySelector("path")?.getAttribute("d")?.length ?? 0).toBeGreaterThan(200);
    expect(screen.getByRole("link", { name: /in der App öffnen/ })).toHaveAttribute("href", "otpauth://totp/x?secret=ABCDEFGHIJKLMNOP");
    fireEvent.change(screen.getByLabelText("Bestätigungscode"), { target: { value: "123 456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    await screen.findByText("Zwei-Faktor-Anmeldung ist aktiv.");
    expect(calls.find((c) => c.path === "/me/totp/confirm")?.body).toEqual({ code: "123456" });
    expect(calls.find((c) => c.path === "/me/totp/setup")?.body).toEqual({ current_password: "geheim123" });
    await screen.findByText("Aktiv");
  });

  it("Mein Konto: Zwei-Faktor einrichten fragt zuerst das Passwort und zeigt dessen Fehler", async () => {
    let attempt = 0;
    const calls = mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", display_name: "", email: null, is_owner: false, locale: "de", permissions: [], totp_enabled: false }),
    });
    const base = globalThis.fetch as unknown as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/me/totp/setup") && init?.method === "POST") {
        calls.push({ method: "POST", path: "/me/totp/setup", body: JSON.parse(init.body as string) });
        attempt += 1;
        if (attempt === 1) return new Response(JSON.stringify({ detail: "Das aktuelle Passwort stimmt nicht." }), { status: 400 });
        return new Response(JSON.stringify({ secret: "ABCDEFGHIJKLMNOP", otpauth_uri: "otpauth://totp/x?secret=ABCDEFGHIJKLMNOP" }), { status: 200 });
      }
      return base(input, init);
    }));
    render(<AccountSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Einrichten" }));
    // Erst die Passwortabfrage, noch kein Aufruf an den Server.
    const next = screen.getByRole("button", { name: "Weiter" });
    expect(next).toBeDisabled();
    expect(calls.some((c) => c.path === "/me/totp/setup")).toBe(false);
    fireEvent.change(screen.getByLabelText(/^Passwort zur Bestätigung/), { target: { value: "falsch" } });
    fireEvent.click(next);
    expect(await screen.findByText("Das aktuelle Passwort stimmt nicht.")).toBeInTheDocument();
    expect(screen.queryByTestId("totp-secret")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Passwort zur Bestätigung/), { target: { value: "richtig" } });
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    expect(await screen.findByTestId("totp-secret")).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/me/totp/setup").map((c) => c.body)).toEqual([
      { current_password: "falsch" },
      { current_password: "richtig" },
    ]);
  });

  it("Mein Konto: Wiederherstellungs-Codes werden einmal gezeigt und lassen sich mit Passwort neu erzeugen", async () => {
    let enabled = false;
    let remaining = 0;
    const calls = mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", display_name: "", email: null, is_owner: false, locale: "de", permissions: [], totp_enabled: enabled, recovery_codes_remaining: remaining }),
      "POST /me/totp/setup": () => ({ secret: "ABCDEFGHIJKLMNOP", otpauth_uri: "otpauth://totp/x?secret=ABCDEFGHIJKLMNOP" }),
      "POST /me/totp/confirm": () => { enabled = true; remaining = 10; return { recovery_codes: ["AAAAA-BBBBB", "CCCCC-DDDDD"] }; },
      "POST /me/recovery-codes": () => { remaining = 10; return { recovery_codes: ["EEEEE-FFFFF", "GGGGG-HHHHH"] }; },
    });
    render(<AccountSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Einrichten" }));
    await enterSetupPassword();
    await screen.findByTestId("totp-secret");
    fireEvent.change(screen.getByLabelText("Bestätigungscode"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));

    const panel = await screen.findByTestId("recovery-codes");
    expect(panel).toHaveTextContent("AAAAA-BBBBB");
    expect(panel).toHaveTextContent("CCCCC-DDDDD");
    fireEvent.click(screen.getByRole("button", { name: "Ich habe die Codes gesichert" }));
    await waitFor(() => expect(screen.queryByTestId("recovery-codes")).not.toBeInTheDocument());
    expect(await screen.findByText(/Noch 10 von 10 Codes übrig/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Neue Wiederherstellungs-Codes erzeugen" }));
    const generate = screen.getByRole("button", { name: "Erzeugen" });
    expect(generate).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Passwort zur Bestätigung/), { target: { value: "geheim123" } });
    fireEvent.click(generate);
    expect(await screen.findByTestId("recovery-codes")).toHaveTextContent("EEEEE-FFFFF");
    expect(calls.find((c) => c.path === "/me/recovery-codes")?.body).toEqual({ current_password: "geheim123" });
  });

  it("Mein Konto: Zwei-Faktor abschalten verlangt das Passwort und zeigt dessen Fehler", async () => {
    let enabled = true;
    let attempt = 0;
    const calls = mockApi({
      "GET /me": () => ({ id: "u1", username: "nico", display_name: "", email: null, is_owner: false, locale: "de", permissions: [], totp_enabled: enabled, recovery_codes_remaining: 4 }),
    });
    const base = globalThis.fetch as unknown as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/me/totp") && init?.method === "DELETE") {
        calls.push({ method: "DELETE", path: "/me/totp", body: JSON.parse(init.body as string) });
        attempt += 1;
        if (attempt === 1) return new Response(JSON.stringify({ detail: "Das aktuelle Passwort stimmt nicht." }), { status: 400 });
        enabled = false;
        return new Response(null, { status: 204 });
      }
      return base(input, init);
    }));
    render(<AccountSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Abschalten" }));
    const confirm = screen.getAllByRole("button", { name: "Abschalten" })[0];
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Passwort zum Abschalten/), { target: { value: "falsch" } });
    fireEvent.click(screen.getByRole("button", { name: "Abschalten" }));
    await screen.findByText("Das aktuelle Passwort stimmt nicht.");
    fireEvent.change(screen.getByLabelText(/^Passwort zum Abschalten/), { target: { value: "richtig" } });
    fireEvent.click(screen.getByRole("button", { name: "Abschalten" }));
    await screen.findByText("Zwei-Faktor-Anmeldung abgeschaltet.");
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.body)).toEqual([{ current_password: "falsch" }, { current_password: "richtig" }]);
  });

  it("Aussehen: Logo-Upload, falscher Bildtyp und Speichern aller Felder", async () => {
    let putBody: Record<string, unknown> | null = null;
    const calls = mockApi({
      "POST /branding/logo": () => ({ ...BRANDING, logo_url: "/api/v1/branding/logo" }),
      "PUT /branding": (_m, body) => { putBody = body as Record<string, unknown>; return body; },
    });
    render(<AppearanceSettings />);
    expect(screen.getByText("kein Logo")).toBeInTheDocument();

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["%PDF"], "x.pdf", { type: "application/pdf" })] } });
    await screen.findByText(/Nicht unterstützter Bildtyp/);
    expect(calls).toHaveLength(0);

    fireEvent.change(input, { target: { files: [new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" })] } });
    await screen.findByText("Logo hochgeladen.");
    expect(useBrandingStore.getState().branding?.logo_url).toBe("/api/v1/branding/logo");

    fireEvent.change(screen.getByDisplayValue("Alt-Name"), { target: { value: "Muster GmbH" } });
    fireEvent.change(screen.getByPlaceholderText("mailto:support@firma.de"), { target: { value: "mailto:it@muster.de" } });
    fireEvent.click(screen.getByRole("button", { name: /Rubin/ }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");
    expect(putBody).toMatchObject({
      product_name: "Muster GmbH", support_url: "mailto:it@muster.de", logo_url: "/api/v1/branding/logo",
      colors: { accent: "#e11d48", accent_strong: "#7c3aed" },
    });
  });

  it("Aussehen: die kleine Zeile unter dem Namen heißt „Untertitel“, nicht „Kurzname“ (so heißt bei Servern die feste Kennung)", () => {
    mockApi({});
    render(<AppearanceSettings />);
    expect(screen.getByLabelText(/^Untertitel/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Kurzname/)).toBeNull();
  });

  it("Aussehen: der Untertitel sagt, dass er auch der App-Name auf dem Handy ist", () => {
    mockApi({});
    render(<AppearanceSettings />);
    expect(screen.getByText(/Name der App auf dem Handy/)).toBeInTheDocument();
  });

  it("Aussehen: ungültige Hex-Farbe wird nicht gespeichert", async () => {
    const calls = mockApi({});
    render(<AppearanceSettings />);
    fireEvent.change(screen.getByLabelText("Akzent (Hex)"), { target: { value: "#12" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText(/Ungültige Farbe/);
    expect(calls).toHaveLength(0);
  });

  it("Automatik: Modus und Risikostufe, eigenes Sperrmuster, Wartungsfenster", async () => {
    const calls = mockApi({
      "GET /settings": () => [
        { key: "autonomy.mode", value: "propose" }, { key: "autonomy.max_risk", value: "low" },
        { key: "security.deny_patterns", value: [] }, { key: "maintenance.windows", value: [] },
      ],
      "GET /hosts": () => [{ id: "h1", name: "pi", display_name: "Raspberry Pi" }],
      "PUT /settings/autonomy.mode": (_m, b) => ({ key: "autonomy.mode", ...(b as object) }),
      "PUT /settings/autonomy.max_risk": (_m, b) => ({ key: "autonomy.max_risk", ...(b as object) }),
      "PUT /settings/security.deny_patterns": (_m, b) => ({ key: "security.deny_patterns", ...(b as object) }),
      "PUT /settings/maintenance.windows": (_m, b) => ({ key: "maintenance.windows", ...(b as object) }),
    });
    render(<AutomationSettings />);
    fireEvent.click(await screen.findByRole("radio", { name: /Selbstständig handeln/ }));
    fireEvent.click(screen.getByRole("button", { name: /Mittel/ }));
    fireEvent.click(screen.getAllByRole("button", { name: "Speichern" })[0]);
    await screen.findByText("Gespeichert.");
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ value: "full" }, { value: "medium" }]);

    fireEvent.change(screen.getByLabelText("Neues Muster"), { target: { value: "(" } });
    fireEvent.click(screen.getByRole("button", { name: "Hinzufügen" }));
    await screen.findByText("Das ist kein gültiger regulärer Ausdruck.");
    fireEvent.change(screen.getByLabelText("Neues Muster"), { target: { value: "docker\\s+volume\\s+rm" } });
    fireEvent.click(screen.getByRole("button", { name: "Hinzufügen" }));
    await screen.findByText("Muster hinzugefügt.");
    expect(calls.at(-1)?.body).toEqual({ value: ["docker\\s+volume\\s+rm"] });

    fireEvent.click(screen.getByRole("button", { name: "Fenster hinzufügen" }));
    const win = within(screen.getByTestId("maintenance-window"));
    fireEvent.click(win.getByRole("button", { name: "Wöchentlich" }));
    fireEvent.change(win.getByLabelText("Stunde"), { target: { value: "2" } });
    expect(win.getByText("Jeden Sonntag um 02:00")).toBeInTheDocument();
    fireEvent.change(win.getByLabelText("Gilt für"), { target: { value: "some" } });
    fireEvent.click(win.getByLabelText("Raspberry Pi"));
    fireEvent.click(screen.getAllByRole("button", { name: "Speichern" }).at(-1)!);
    await screen.findByText("Wartungsfenster gespeichert.");
    expect(calls.at(-1)?.body).toEqual({ value: [{ cron: "0 2 * * 0", duration_minutes: 60, host_ids: ["h1"] }] });
  });

  it("Erweiterungen: mitgelieferte zeigen die Programmversion, nachinstallierte ihre eigene, ältere Server v<Version>", async () => {
    const base = { state: "enabled", description: null, icon: null, granted_permissions: [], last_error: null };
    mockApi({
      "GET /extensions": () => [
        { ...base, id: "eigen", name: "Mitgeliefert", version: "0.1.0", source: "bundled", bundled: true, display_version: "0.6.0" },
        { ...base, id: "fremd", name: "Fremd", version: "2.3.4", source: "pip", bundled: false, display_version: "2.3.4" },
        { ...base, id: "alt", name: "Alt", version: "0.1.0", source: "bundled" },
      ],
    });
    render(<ExtensionsSettings />, { wrapper: QueryWrapper });
    expect((await screen.findByTestId("ext-version-eigen")).textContent).toBe("Teil von Nodvard Deck 0.6.0");
    expect(screen.getByTestId("ext-version-fremd").textContent).toBe("v2.3.4");
    expect(screen.getByTestId("ext-version-alt").textContent).toBe("v0.1.0");
    expect(screen.queryByText("v0.1.0", { selector: "[data-testid=ext-version-eigen]" })).toBeNull();
  });

  it("Erweiterungen: Ausschalten fragt nach und ruft disable auf", async () => {
    let state = "enabled";
    const calls = mockApi({
      "GET /extensions": () => [{
        id: "nexus-soc", version: "1.0.0", state, source: "bundled", name: "Nodvard Shield", description: "Sicherheitsvorfälle",
        icon: "shield", granted_permissions: [], last_error: null,
      }],
      "POST /extensions/nexus-soc/disable": () => { state = "disabled"; return { id: "nexus-soc", state }; },
    });
    render(<><ExtensionsSettings /><GlobalDialogs /></>, { wrapper: QueryWrapper });
    fireEvent.click(await screen.findByRole("switch", { name: "Nodvard Shield ausschalten" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
    await screen.findByText("„Nodvard Shield“ ausgeschaltet.");
    await waitFor(() => expect(screen.getByRole("switch", { name: "Nodvard Shield einschalten" })).toBeInTheDocument());
    expect(calls.some((c) => c.path === "/extensions/nexus-soc/disable")).toBe(true);
  });

  it("Erweiterungen: defekte, inkompatible und entfernte Module heißen nicht „Aus“", async () => {
    const row = (id: string, state: string, last_error: string | null = null) => ({
      id, version: "1.0.0", state, source: "bundled", name: id, description: null, icon: null, granted_permissions: [], last_error,
    });
    mockApi({
      "GET /extensions": () => [
        row("kaputt", "error", "on_start() fehlgeschlagen: boom"),
        row("alt", "incompatible", "api_version '0.1' inkompatibel"),
        row("weg", "uninstalling"),
        row("aus", "disabled"),
        row("an", "enabled"),
      ],
    });
    render(<><ExtensionsSettings /><GlobalDialogs /></>, { wrapper: QueryWrapper });
    const badge = async (id: string) => within(await screen.findByTestId(`ext-${id}`));
    expect((await badge("kaputt")).getByText("Fehler")).toBeInTheDocument();
    expect((await badge("kaputt")).queryByText("Aus")).toBeNull();
    expect((await badge("alt")).getByText("Nicht kompatibel")).toBeInTheDocument();
    expect((await badge("weg")).getByText("Wird entfernt")).toBeInTheDocument();
    expect((await badge("weg")).getByRole("switch")).toBeDisabled();
    expect((await badge("aus")).getByText("Aus")).toBeInTheDocument();
    expect((await badge("an")).getByText("Aktiv")).toBeInTheDocument();
  });

  it("Erweiterungen: eine Erweiterung im Fehlerstatus lässt sich ausschalten (eigener Knopf ruft disable auf)", async () => {
    let state = "error";
    const calls = mockApi({
      "GET /extensions": () => [{
        id: "nexus-soc", version: "1.0.0", state, source: "bundled", name: "Nodvard Shield", description: null,
        icon: "shield", granted_permissions: [], last_error: state === "error" ? "on_start() fehlgeschlagen: boom" : null,
      }],
      "POST /extensions/nexus-soc/disable": () => { state = "disabled"; return { id: "nexus-soc", state }; },
    });
    render(<><ExtensionsSettings /><GlobalDialogs /></>, { wrapper: QueryWrapper });
    const row = within(await screen.findByTestId("ext-nexus-soc"));
    // Der Schalter zeigt „aus“ und startet nur neu -- ausschalten geht ueber den eigenen Knopf.
    fireEvent.click(row.getByRole("button", { name: "Ausschalten" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
    await screen.findByText("„Nodvard Shield“ ausgeschaltet.");
    expect(calls.some((c) => c.path === "/extensions/nexus-soc/disable")).toBe(true);
    expect(calls.some((c) => c.path === "/extensions/nexus-soc/enable")).toBe(false);
    await waitFor(() => expect(within(screen.getByTestId("ext-nexus-soc")).queryByRole("button", { name: "Ausschalten" })).toBeNull());
  });

  it("Erweiterungen: der Knopf „Ausschalten“ erscheint nur im Fehlerstatus", async () => {
    const row = (id: string, state: string) => ({
      id, version: "1.0.0", state, source: "bundled", name: id, description: null, icon: null, granted_permissions: [], last_error: null,
    });
    mockApi({ "GET /extensions": () => [row("aus", "disabled"), row("an", "enabled"), row("kaputt", "error")] });
    render(<><ExtensionsSettings /><GlobalDialogs /></>, { wrapper: QueryWrapper });
    await screen.findByTestId("ext-kaputt");
    expect(within(screen.getByTestId("ext-kaputt")).getByRole("button", { name: "Ausschalten" })).toBeInTheDocument();
    expect(within(screen.getByTestId("ext-aus")).queryByRole("button", { name: "Ausschalten" })).toBeNull();
    expect(within(screen.getByTestId("ext-an")).queryByRole("button", { name: "Ausschalten" })).toBeNull();
  });

  it("Erweiterungen: scheitert das Einschalten, steht der Grund da statt „eingeschaltet“", async () => {
    let state = "disabled";
    let last_error: string | null = null;
    mockApi({
      "GET /extensions": () => [{
        id: "nexus-soc", version: "1.0.0", state, source: "bundled", name: "Nodvard Shield", description: null,
        icon: "shield", granted_permissions: [], last_error,
      }],
      "POST /extensions/nexus-soc/enable": () => {
        state = "error";
        last_error = "setup() fehlgeschlagen: boom";
        return { id: "nexus-soc", state, last_error, name: "Nodvard Shield" };
      },
    });
    render(<><ExtensionsSettings /><GlobalDialogs /></>, { wrapper: QueryWrapper });
    fireEvent.click(await screen.findByRole("switch", { name: "Nodvard Shield einschalten" }));
    expect(await screen.findByText("„Nodvard Shield“ ließ sich nicht einschalten: setup() fehlgeschlagen: boom")).toBeInTheDocument();
    expect(screen.queryByText("„Nodvard Shield“ eingeschaltet.")).toBeNull();
    await waitFor(() => expect(within(screen.getByTestId("ext-nexus-soc")).getByText("Fehler")).toBeInTheDocument());
  });
});


function QueryWrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

describe("Erweiterung konfigurieren", () => {
  const SCHEMA = {
    type: "object",
    properties: {
      connections: {
        type: "array", title: "Proxmox-Server", "x-item-title": "Server",
        items: {
          type: "object",
          properties: {
            name: { type: "string", title: "Kurzname" },
            base_url: { type: "string", title: "Adresse" },
            tls_insecure_skip_verify: { type: "boolean", title: "Selbstsigniertes Zertifikat erlauben" },
          },
          required: ["name", "base_url"],
        },
      },
      suppressed_hosts: { type: "array", items: { type: "string" }, title: "Ignorierte Server", "x-advanced": true },
      mode: { type: "string", enum: ["http", "https"], title: "Link-Schema", "x-enum-labels": { https: "Verschlüsselt (https)" } },
    },
  };

  function renderConfig() {
    return render(
      <MemoryRouter initialEntries={["/settings/extensions/proxmox"]}>
        <Routes><Route path="/settings/extensions/:extId" element={<ExtensionConfigPage />} /></Routes>
      </MemoryRouter>,
    );
  }

  it("baut das Formular aus dem Schema und speichert Listen, Schalter und erweiterte Felder", async () => {
    const calls = mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: "Server einlesen", icon: "server", state: "enabled", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({ schema: SCHEMA, values: { internal: 1 }, secrets: [] }),
      "PUT /extensions/proxmox/settings": (_m, body) => ({
        schema: SCHEMA, values: (body as { values: object }).values,
        secrets: [{ label: "proxmox-token:pve2", title: "API-Token", description: null, item: "pve2", is_set: false }],
      }),
      "PUT /extensions/proxmox/secrets": () => undefined,
    });
    renderConfig();
    expect(await screen.findByText("Noch kein Server eingetragen.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Server hinzufügen" }));
    fireEvent.change(screen.getByLabelText(/^Kurzname/), { target: { value: "pve2" } });
    fireEvent.change(screen.getByLabelText(/^Adresse/), { target: { value: "https://pve:8006" } });
    fireEvent.click(screen.getByRole("switch", { name: "Selbstsigniertes Zertifikat erlauben" }));
    expect(screen.getByRole("option", { name: "Verschlüsselt (https)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "http" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Link-Schema"), { target: { value: "https" } });
    fireEvent.click(screen.getByRole("button", { name: /Erweitert/ }));
    const words = screen.getByLabelText("Ignorierte Server");
    fireEvent.change(words, { target: { value: "pihole" } });
    fireEvent.keyDown(words, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Einstellungen gespeichert.");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      values: {
        mode: "https", suppressed_hosts: ["pihole"],
        connections: [{ name: "pve2", base_url: "https://pve:8006", tls_insecure_skip_verify: true }],
      },
    });

    fireEvent.change(screen.getByLabelText("API-Token – pve2"), { target: { value: "geheim" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Speichern" }).at(-1)!);
    await screen.findByText("Hinterlegt");
    expect(calls.find((c) => c.path === "/extensions/proxmox/secrets")).toMatchObject({ method: "PUT", body: { label: "proxmox-token:pve2", value: "geheim" } });
  });

  it("schickt nur Geändertes (nie versteckte Felder), meldet gelöschte Zugangsdaten und löscht Entferntes per null", async () => {
    const schema = {
      type: "object",
      properties: {
        server_url: { type: "string", title: "Adresse" },
        topic: { type: "string", title: "Thema" },
        servers: { type: "object", "x-hidden": true },
      },
      "x-secrets": [{ label: "tk", title: "Zugriffstoken", "x-secret-bound-to": ["server_url"] }],
    };
    const calls = mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: null, icon: "server", state: "enabled", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({
        schema, values: { server_url: "https://a", topic: "t", servers: { h1: { profile: "x" } } },
        secrets: [{ label: "tk", title: "Zugriffstoken", description: null, item: null, is_set: true }],
      }),
      "PUT /extensions/proxmox/settings": () => ({
        schema, values: { server_url: "https://b", servers: { h1: { profile: "neu" } } },
        secrets: [{ label: "tk", title: "Zugriffstoken", description: null, item: null, is_set: false }],
        secrets_cleared: ["tk"],
      }),
    });
    renderConfig();
    fireEvent.change(await screen.findByLabelText(/^Adresse/), { target: { value: "https://b" } });
    fireEvent.change(screen.getByLabelText(/^Thema/), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText(/diese Zugangsdaten gelöscht: Zugriffstoken/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ values: { server_url: "https://b", topic: null } });
  });

  it("sperrt das Eintragen eines Zugangs, solange eine geänderte Adresse ihn beim Speichern wieder löschen würde", async () => {
    const schema = {
      type: "object",
      properties: { server_url: { type: "string", title: "Adresse" }, topic: { type: "string", title: "Thema" } },
      "x-secrets": [{ label: "tk", title: "Zugriffstoken", "x-secret-bound-to": ["server_url"] }],
    };
    mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: null, icon: "server", state: "enabled", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({
        schema, values: { server_url: "https://a", topic: "t" },
        secrets: [{ label: "tk", title: "Zugriffstoken", description: null, item: null, is_set: true }],
      }),
    });
    renderConfig();
    fireEvent.click(await screen.findByRole("button", { name: "Ersetzen" }));
    fireEvent.change(screen.getByLabelText("Zugriffstoken"), { target: { value: "neu" } });
    const replace = () => within(screen.getByTestId("secret-tk")).getByRole("button", { name: /Ersetzen/ });
    expect(replace()).toBeEnabled();
    // Nur das Thema geändert: Adresse gleich, nichts gesperrt.
    fireEvent.change(screen.getByLabelText(/^Thema/), { target: { value: "anders" } });
    expect(replace()).toBeEnabled();
    expect(screen.queryByText(/Beim Speichern werden diese Zugangsdaten gelöscht/)).not.toBeInTheDocument();
    // Neue Adresse: erst speichern, dann das Token.
    fireEvent.change(screen.getByLabelText(/^Adresse/), { target: { value: "https://b" } });
    expect(replace()).toBeDisabled();
    expect(screen.getByText(/Beim Speichern werden diese Zugangsdaten gelöscht.*Zugriffstoken/)).toBeInTheDocument();
    expect(screen.getByText(/Erst die Einstellungen mit der neuen Adresse speichern/)).toBeInTheDocument();
    // Nur ein Schlussstrich dazu zählt nicht als neue Adresse, andere Schreibweisen derselben Adresse auch nicht.
    fireEvent.change(screen.getByLabelText(/^Adresse/), { target: { value: "https://a/" } });
    expect(replace()).toBeEnabled();
    fireEvent.change(screen.getByLabelText(/^Adresse/), { target: { value: "HTTPS://A:443//" } });
    expect(replace()).toBeEnabled();
    expect(screen.queryByText(/Beim Speichern werden diese Zugangsdaten gelöscht/)).not.toBeInTheDocument();
    // Ein anderer Pfad schon (im Zweifel ein neues Ziel).
    fireEvent.change(screen.getByLabelText(/^Adresse/), { target: { value: "https://a/anderes" } });
    expect(replace()).toBeDisabled();
  });

  it("sperrt auch, wenn nur eines von mehreren gebundenen Feldern neu eingetragen wird (Ersatz-Server)", async () => {
    const schema = {
      type: "object",
      properties: { url: { type: "string", title: "Server" }, failover_url: { type: "string", title: "Ersatz-Server" } },
      "x-secrets": [{ label: "key", title: "Schlüssel", optional: true, "x-secret-bound-to": ["url", "failover_url"] }],
    };
    mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: null, icon: "server", state: "enabled", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({
        schema, values: { url: "http://haupt:11434" },
        secrets: [{ label: "key", title: "Schlüssel", description: null, item: null, is_set: true, optional: true }],
      }),
    });
    renderConfig();
    await screen.findByLabelText(/^Server/);
    expect(screen.queryByText(/Beim Speichern werden diese Zugangsdaten gelöscht/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Ersatz-Server/), { target: { value: "http://eigener-server:11434" } });
    expect(screen.getByText(/Beim Speichern werden diese Zugangsdaten gelöscht.*Schlüssel/)).toBeInTheDocument();
  });

  it("lässt beim ersten Einrichten erst die Adresse speichern, dann die Zugangsdaten eintragen", async () => {
    const schema = {
      type: "object",
      properties: { server_url: { type: "string", title: "Adresse" } },
      "x-secrets": [{ label: "tk", title: "Zugriffstoken", "x-secret-bound-to": ["server_url"] }],
    };
    const slot = { label: "tk", title: "Zugriffstoken", description: null, item: null, is_set: false };
    mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: null, icon: "server", state: "enabled", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({ schema, values: {}, secrets: [slot] }),
      "PUT /extensions/proxmox/settings": () => ({ schema, values: { server_url: "https://b" }, secrets: [slot] }),
    });
    renderConfig();
    const save = () => within(screen.getByTestId("secret-tk")).getByRole("button", { name: /Speichern/ });
    await screen.findByLabelText(/^Adresse/);
    fireEvent.change(screen.getByLabelText("Zugriffstoken"), { target: { value: "neu" } });
    expect(save()).toBeDisabled();
    expect(screen.getByText(/Erst die Adresse eintragen und die Einstellungen speichern/)).toBeInTheDocument();

    // Adresse getippt, aber noch nicht gespeichert: der Server würde den Wert ablehnen.
    fireEvent.change(screen.getByLabelText(/^Adresse/), { target: { value: "https://b" } });
    expect(save()).toBeDisabled();

    fireEvent.click(screen.getAllByRole("button", { name: "Speichern" })[0]);
    await screen.findByText("Einstellungen gespeichert.");
    expect(save()).toBeEnabled();
    expect(screen.queryByText(/Erst die Adresse eintragen und die Einstellungen speichern/)).not.toBeInTheDocument();
  });

  it("ein Zugang mit Standardadresse aus dem Schema ist sofort frei", async () => {
    const schema = {
      type: "object",
      properties: { server_url: { type: "string", title: "Adresse", default: "https://standard.example" } },
      "x-secrets": [{ label: "tk", title: "Zugriffstoken", "x-secret-bound-to": ["server_url"] }],
    };
    mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: null, icon: "server", state: "enabled", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({
        schema, values: {}, secrets: [{ label: "tk", title: "Zugriffstoken", description: null, item: null, is_set: false }],
      }),
    });
    renderConfig();
    fireEvent.change(await screen.findByLabelText("Zugriffstoken"), { target: { value: "neu" } });
    expect(within(screen.getByTestId("secret-tk")).getByRole("button", { name: /Speichern/ })).toBeEnabled();
  });

  it("zeigt „Fehler“ für eine defekte Erweiterung", async () => {
    mockApi({
      "GET /extensions/proxmox": () => ({ id: "proxmox", name: "Proxmox VE", description: null, icon: "server", state: "error", version: "0.1.0" }),
      "GET /extensions/proxmox/settings": () => ({ schema: null, values: {}, secrets: [] }),
    });
    renderConfig();
    expect(await screen.findByText("Fehler")).toBeInTheDocument();
    expect(screen.queryByText("Aus")).toBeNull();
  });
});

describe("Protokoll", () => {
  it("nennt Änderungen an Servern und Zugängen in verständlichem Deutsch", async () => {
    const actions = [
      ["host.created", "Server angelegt"], ["host.updated", "Server geändert"], ["host.deleted", "Server gelöscht"],
      ["host.credential_added", "SSH-Zugang hinterlegt"], ["host.credential_deleted", "SSH-Zugang gelöscht"],
      ["host.key_generated", "SSH-Schlüssel erzeugt"], ["host.known_key_pinned", "Server-Schlüssel bestätigt"],
      ["host.known_key_forgotten", "Server-Schlüssel vergessen"], ["host.connection_checked", "Verbindung geprüft"],
      ["host.group_changed", "Gruppe geändert"], ["host.credential_made_default", "Standard-Zugang umgestellt"],
      ["extension.enabled", "Erweiterung eingeschaltet"], ["extension.disabled", "Erweiterung ausgeschaltet"],
      ["extension.auto_enabled", "Erweiterung automatisch eingeschaltet"],
    ];
    mockApi({
      "GET /audit": () => actions.map(([action], i) => ({
        id: `a${i}`, ts: "2026-09-30T10:00:00Z", actor_type: "user", actor_id: "u1", action, target_type: "host",
        target_id: "h1", outcome: "success", reason: null, detail: {}, ip: null, user_agent: null,
      })),
      "GET /users": () => [{ id: "u1", username: "nico" }],
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
    for (const [, label] of actions) expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("nennt Dateizugriffe auf Servern in verständlichem Deutsch", async () => {
    const actions = [
      ["files.access", "Zugriff auf Dateien eines Servers"], ["files.download", "Datei heruntergeladen"],
      ["files.upload", "Datei hochgeladen"], ["files.mkdir", "Ordner angelegt"], ["files.rename", "Datei umbenannt"],
      ["files.remove", "Datei gelöscht"], ["files.search", "Server nach Dateien durchsucht"],
      ["files.transfer_start", "Kopieren gestartet"], ["files.transfer", "Datei kopiert"],
    ];
    mockApi({
      "GET /audit": () => actions.map(([action], i) => ({
        id: `f${i}`, ts: "2026-09-30T10:00:00Z", actor_type: "user", actor_id: "u1", action, target_type: "file_source",
        target_id: "ssh-sftp:pve1", outcome: "success", reason: null, detail: { path: "/etc/hosts" }, ip: null, user_agent: null,
      })),
      "GET /users": () => [{ id: "u1", username: "bastler" }],
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
    for (const [, label] of actions) expect(await screen.findByText(label)).toBeInTheDocument();
  });
});

describe("Protokoll ohne Server-Rechte", () => {
  it("zeigt bei weggelassener Ausgabe einen Hinweis", async () => {
    mockApi({
      "GET /audit": () => [{
        id: "a1", ts: "2026-09-30T10:00:00Z", actor_type: "system", actor_id: "gate", action: "action.executed", target_type: "action",
        target_id: "x1", outcome: "success", reason: null, detail: { action_type: "shell.exec", result: { success: true, exit_code: 0 } },
        output_hidden: true, ip: null, user_agent: null,
      }],
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
    fireEvent.click(await screen.findByText("Aktion ausgeführt"));
    expect(await screen.findByText("Ausgabe nur für Nutzer mit Server-Rechten sichtbar")).toBeInTheDocument();
  });
});

describe("Protokoll: Namen statt Codes und Nummern", () => {
  const entry = (over: Record<string, unknown>) => ({
    id: "a1", ts: "2026-10-01T10:00:00Z", actor_type: "user", actor_id: "u1", action: "login.succeeded", target_type: "user",
    target_id: "u1", outcome: "success", reason: null, detail: {}, ip: null, user_agent: null, ...over,
  });

  function mockAudit(entries: unknown[]) {
    mockApi({
      "GET /audit": () => entries,
      "GET /users": () => [{ id: "u1", username: "nico" }],
      "GET /hosts": () => [{ id: "0c1b5f8e-aaaa-bbbb-cccc-000000000001", name: "bastel-pi", display_name: "Bastel-Pi" }],
      "GET /extensions": () => [{ id: "nexus-soc", name: "Nodvard Shield", state: "enabled" }],
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
  }

  it("der technische Name steht nicht mehr als Zeile unter dem Vorgang, nur noch als Tooltip", async () => {
    mockAudit([entry({})]);
    const label = await screen.findByText("Anmeldung");
    expect(label).toHaveAttribute("title", "login.succeeded");
    expect(document.body.textContent).not.toContain("login.succeeded");
  });

  it("Ziel mit Namen: „Benutzer · nico“ und „Server · Bastel-Pi“, nie die lange Nummer", async () => {
    mockAudit([
      entry({ id: "a1" }),
      entry({ id: "a2", action: "host.created", target_type: "host", target_id: "0c1b5f8e-aaaa-bbbb-cccc-000000000001" }),
      entry({ id: "a3", action: "host.created", target_type: "host", target_id: "0c1b5f8e-aaaa-bbbb-cccc-0000000000ff" }),
    ]);
    await screen.findAllByText("Anmeldung");
    await waitFor(() => expect(document.body.textContent).toContain("Server · Bastel-Pi"));
    expect(document.body.textContent).toContain("Benutzer · nico");
    // Ein gelöschter Server: nur die Art, keine unlesbare Nummer in der Tabelle.
    const cells = [...document.querySelectorAll("tbody tr td:nth-child(5)")].map((td) => td.textContent);
    expect(cells).toEqual(["Benutzer · nico", "Server · Bastel-Pi", "Server"]);
    expect(document.body.textContent).not.toMatch(/user ·|host ·/);
  });

  it("Erweiterungen mit ihrem Namen, die Nummer steht in den Einzelheiten", async () => {
    mockAudit([entry({ action: "extension.enabled", target_type: "extension", target_id: "nexus-soc" })]);
    await waitFor(() => expect(document.body.textContent).toContain("Erweiterung · Nodvard Shield"));
    fireEvent.click(screen.getByText("Erweiterung eingeschaltet"));
    expect(screen.getByText("extension.enabled")).toBeInTheDocument(); // Einzelheiten: der technische Name
    expect(screen.getByText("Erweiterung · nexus-soc")).toBeInTheDocument(); // Einzelheiten: die Kennung
  });

  it("targetText: Art auf Deutsch, Namen wo bekannt", () => {
    const names = { users: { u1: "nico" }, hosts: {}, extension: (id: string) => id };
    expect(targetText({ target_type: null, target_id: null }, names)).toBe("–");
    expect(targetText({ target_type: "setting", target_id: "system.timezone" }, names)).toBe("Einstellung · system.timezone");
    expect(targetText({ target_type: "action", target_id: "0c1b5f8e-aaaa-bbbb-cccc-000000000009" }, names)).toBe("Aktion");
    expect(targetText({ target_type: "backup", target_id: null }, names)).toBe("Sicherung");
    expect(targetText({ target_type: "gibtsnicht", target_id: "x" }, names)).toBe("gibtsnicht · x");
  });
});

describe("System: Zeit & Protokoll", () => {
  beforeEach(() => {
    window.__lattice ??= {} as Window["__lattice"];
    setDeckTimezone(null);
    // Nur das Recht dieser Karte: die Karte „Sicherung“ (system.read) hat eigene Tests.
    setUser(["settings.write"]);
  });

  // Eine Zone, die sicher nicht die des Geräts ist -- sonst gäbe es keinen Vorschlag.
  const SAVED = browserTimeZone() === "Asia/Tokyo" ? "Europe/Berlin" : "Asia/Tokyo";
  const settingsRows = () => [
    { key: "autonomy.mode", value: "propose" },
    { key: "system.timezone", value: SAVED },
    { key: "audit.retention_days", value: 90 },
    { key: "jobs.run_retention_days", value: 30 },
  ];

  it("zeigt die gespeicherten Werte und speichert Zone und Aufbewahrung", async () => {
    const calls = mockApi({
      "GET /settings": settingsRows,
      "PUT /settings/system.timezone": (_m, body) => ({ key: "system.timezone", value: (body as { value: string }).value }),
      "PUT /settings/audit.retention_days": (_m, body) => ({ key: "audit.retention_days", value: (body as { value: number }).value }),
    });
    render(<SystemSettings />);
    expect(await screen.findByText("Zeit & Protokoll")).toBeInTheDocument();
    expect(screen.getByDisplayValue("90")).toBeInTheDocument();
    expect(within(screen.getByRole("listbox", { name: "Zeitzonen" })).getByRole("option", { selected: true })).toHaveTextContent(SAVED);

    fireEvent.change(screen.getByLabelText("Zeitzone suchen"), { target: { value: "new york" } });
    const options = within(screen.getByRole("listbox", { name: "Zeitzonen" })).getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0].textContent).toMatch(/^America\/New_York/);
    fireEvent.click(within(options[0]).getByRole("button"));
    expect(screen.getByText("nicht gespeichert")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Protokoll aufbewahren (Tage)"), { target: { value: "365" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");

    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.path, c.body])).toEqual([
      ["/settings/system.timezone", { value: "America/New_York" }],
      ["/settings/audit.retention_days", { value: 365 }],
    ]);
    // Die Zeitplan-Wähler rechnen sofort in der neuen Zone.
    expect(getDeckTimezone()).toBe("America/New_York");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("speichert nur, was sich geändert hat", async () => {
    const calls = mockApi({
      "GET /settings": settingsRows,
      "PUT /settings/audit.retention_days": () => ({}),
    });
    render(<SystemSettings />);
    const save = await screen.findByRole("button", { name: "Speichern" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Protokoll aufbewahren (Tage)"), { target: { value: "30" } });
    fireEvent.click(save);
    await screen.findByText("Gespeichert.");
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.path)).toEqual(["/settings/audit.retention_days"]);
    expect(getDeckTimezone()).toBeNull();
  });

  it("lässt nur 7 bis 3650 Tage zu", async () => {
    const calls = mockApi({ "GET /settings": settingsRows });
    render(<SystemSettings />);
    const input = await screen.findByLabelText("Protokoll aufbewahren (Tage)");
    for (const bad of ["6", "3651", "", "abc", "12,5"]) {
      fireEvent.change(input, { target: { value: bad } });
      expect(screen.getByRole("alert")).toHaveTextContent("zwischen 7 und 3650");
      expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    }
    for (const good of ["7", "3650"]) {
      fireEvent.change(input, { target: { value: good } });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
    }
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("zeigt den Job-Verlauf neben dem Protokoll und speichert ihn einzeln", async () => {
    const calls = mockApi({
      "GET /settings": () => settingsRows().map((r) => (r.key === "jobs.run_retention_days" ? { ...r, value: 45 } : r)),
      "PUT /settings/jobs.run_retention_days": (_m, body) => ({ key: "jobs.run_retention_days", value: (body as { value: number }).value }),
    });
    render(<SystemSettings />);
    const runs = (await screen.findByLabelText("Job-Verlauf aufbewahren (Tage)")) as HTMLInputElement;
    expect(runs.value).toBe("45");
    expect(screen.getByLabelText("Protokoll aufbewahren (Tage)")).toHaveValue(90);
    expect(screen.getByText(/Zwischen 1 und 3650 Tagen/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();

    fireEvent.change(runs, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Gespeichert.");

    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.path, c.body])).toEqual([
      ["/settings/jobs.run_retention_days", { value: 7 }],
    ]);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("nimmt 30 Tage an, wenn der Server den Job-Verlauf noch nicht meldet", async () => {
    mockApi({ "GET /settings": () => settingsRows().filter((r) => r.key !== "jobs.run_retention_days") });
    render(<SystemSettings />);
    expect(await screen.findByLabelText("Job-Verlauf aufbewahren (Tage)")).toHaveValue(30);
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("lässt beim Job-Verlauf nur 1 bis 3650 Tage zu", async () => {
    const calls = mockApi({ "GET /settings": settingsRows });
    render(<SystemSettings />);
    const input = await screen.findByLabelText("Job-Verlauf aufbewahren (Tage)");
    for (const bad of ["0", "3651", "", "abc", "1,5", "-3"]) {
      fireEvent.change(input, { target: { value: bad } });
      expect(screen.getByRole("alert")).toHaveTextContent("zwischen 1 und 3650");
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    }
    for (const good of ["1", "3650"]) {
      fireEvent.change(input, { target: { value: good } });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();
    }
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("speichert Protokoll und Job-Verlauf zusammen, ein Fehler beim zweiten merkt sich das erste", async () => {
    const puts: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api\/v1/, "");
      if (path === "/settings") return new Response(JSON.stringify(settingsRows()), { status: 200 });
      puts.push(`${init?.method ?? "GET"} ${path}`);
      if (path === "/settings/audit.retention_days") return new Response(JSON.stringify({}), { status: 200 });
      return new Response(JSON.stringify({ detail: "jobs.run_retention_days muss zwischen 1 und 3650 Tagen liegen." }), { status: 422 });
    }));
    render(<SystemSettings />);
    fireEvent.change(await screen.findByLabelText("Protokoll aufbewahren (Tage)"), { target: { value: "120" } });
    fireEvent.change(screen.getByLabelText("Job-Verlauf aufbewahren (Tage)"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("jobs.run_retention_days muss zwischen 1 und 3650");
    expect(puts).toEqual(["PUT /settings/audit.retention_days", "PUT /settings/jobs.run_retention_days"]);
    // Erneut speichern: das Protokoll ist schon gespeichert und geht nicht noch einmal raus.
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(puts).toHaveLength(3));
    expect(puts[2]).toBe("PUT /settings/jobs.run_retention_days");
  });

  it("schlägt die Zone des Geräts vor", async () => {
    mockApi({ "GET /settings": settingsRows });
    render(<SystemSettings />);
    const hint = (await screen.findByText(/Dieses Gerät steht in/)).closest("p")!;
    expect(hint).toHaveTextContent(browserTimeZone());
    fireEvent.click(within(hint).getByRole("button", { name: "Diese Zone übernehmen" }));
    expect(screen.getByText("nicht gespeichert")).toBeInTheDocument();
    expect(screen.queryByText(/Dieses Gerät steht in/)).not.toBeInTheDocument();
  });

  it("zeigt einen Fehler des Servers und merkt sich, was schon gespeichert ist", async () => {
    const fn = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^\/api\/v1/, "");
      if (path === "/settings") return new Response(JSON.stringify(settingsRows()), { status: 200 });
      if (path === "/settings/system.timezone") return new Response(JSON.stringify({}), { status: 200 });
      return new Response(JSON.stringify({ detail: "audit.retention_days muss zwischen 7 und 3650 Tagen liegen." }), { status: 422 });
    });
    vi.stubGlobal("fetch", fn);
    render(<SystemSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "Diese Zone übernehmen" }));
    fireEvent.change(screen.getByLabelText("Protokoll aufbewahren (Tage)"), { target: { value: "400" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("muss zwischen 7 und 3650");
    expect(getDeckTimezone()).toBe(browserTimeZone());
  });

  it("Zonensuche: findet ohne Rücksicht auf Groß-/Kleinschreibung und Unterstriche", () => {
    const zones = timezoneOptions(["Mars/Olympus"]);
    expect(zones).toContain("UTC");
    expect(zones).toContain("Europe/Berlin");
    expect(zones).toContain("Mars/Olympus");
    expect(filterTimezones(zones, "BERLIN")).toContain("Europe/Berlin");
    expect(filterTimezones(zones, "america new york")).toEqual(["America/New_York"]);
    expect(filterTimezones(zones, "  ")).toHaveLength(zones.length);
    expect(filterTimezones(zones, "gibt-es-nicht")).toEqual([]);
  });
});
