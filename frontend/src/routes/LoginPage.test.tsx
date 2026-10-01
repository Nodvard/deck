import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";
import { LoginPage } from "./LoginPage";

// Einzelne Tests ersetzen login/submitMfa im Store -- vor jedem Test die echten zurueck.
const { login: realLogin, submitMfa: realSubmitMfa } = useAuthStore.getState();

function renderLogin(fetchImpl?: (input: RequestInfo | URL) => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(fetchImpl ?? (async () => new Response(JSON.stringify({ needed: false }), { status: 200 }))));
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <LoginPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: null, login: realLogin, submitMfa: realSubmitMfa } as never);
  useBrandingStore.setState({
    loaded: true,
    branding: {
      product_name: "Mein Homelab", short_name: "Homelab-Cockpit", logo_url: null, favicon_url: null,
      login_subtitle: "Alles für den Homelab an einer Stelle.", support_url: "https://example.org/hilfe",
      colors: { accent: "#e11d48", accent_strong: "#7c3aed", background: "#0b0f17", surface: "#121826", text: "#e5e7eb" },
    },
  });
});

describe("LoginPage", () => {
  it("zeigt Produktname, Untertitel und Support-Link aus dem Branding", () => {
    renderLogin();
    expect(screen.getByText("Willkommen zurück bei Mein Homelab.")).toBeInTheDocument();
    expect(screen.getByText("Alles für den Homelab an einer Stelle.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Support kontaktieren" })).toHaveAttribute("href", "https://example.org/hilfe");
  });

  it("prüft leere Felder, zeigt Fehler vom Server und kann das Passwort anzeigen", async () => {
    const login = vi.fn().mockResolvedValue({ ok: false, error: "Benutzername oder Passwort falsch." });
    useAuthStore.setState({ login } as never);
    renderLogin();

    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte Benutzername und Passwort eingeben.");
    expect(login).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: " Admin " } });
    const password = screen.getByLabelText("Passwort");
    fireEvent.change(password, { target: { value: "geheim" } });
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Passwort anzeigen" }));
    expect(password).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("admin", "geheim"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Benutzername oder Passwort falsch.");
  });

  it("zweiter Faktor: Code ohne Leerzeichen senden, zurück zur Anmeldung möglich", async () => {
    const submitMfa = vi.fn().mockResolvedValue({ ok: true });
    useAuthStore.setState({ mfaToken: "mfa", submitMfa } as never);
    renderLogin();
    fireEvent.change(screen.getByLabelText("Sechsstelliger Code"), { target: { value: "123 456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    await waitFor(() => expect(submitMfa).toHaveBeenCalledWith("123456"));

    fireEvent.click(screen.getByRole("button", { name: /Zurück zur Anmeldung/ }));
    expect(useAuthStore.getState().mfaToken).toBeNull();
    expect(screen.getByLabelText("Benutzername")).toBeInTheDocument();
  });

  it("zweiter Faktor: Wiederherstellungs-Code statt App-Code eingeben", async () => {
    const submitMfa = vi.fn().mockResolvedValue({ ok: true });
    useAuthStore.setState({ mfaToken: "mfa", submitMfa } as never);
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: /Wiederherstellungs-Code verwenden/ }));
    fireEvent.change(screen.getByLabelText("Wiederherstellungs-Code"), { target: { value: "abcde-fghjk" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    await waitFor(() => expect(submitMfa).toHaveBeenCalledWith("ABCDE-FGHJK"));

    fireEvent.click(screen.getByRole("button", { name: /Stattdessen Code aus der App/ }));
    expect(screen.getByLabelText("Sechsstelliger Code")).toBeInTheDocument();
  });

  /** Container startet gerade neu: fetch wirft (Verbindung abgelehnt), nur /auth/bootstrap klappte vorher. */
  const offline = async (input: RequestInfo | URL) => {
    if (String(input) === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
    throw new TypeError("Failed to fetch");
  };

  it("Server nicht erreichbar: Hinweis statt endlos „Bitte warten …“", async () => {
    renderLogin(offline);
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "geheim" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Server nicht erreichbar – bitte gleich noch einmal versuchen.");
    expect(screen.getByRole("button", { name: "Anmelden" })).toBeEnabled();
  });

  it("zweiter Faktor bei nicht erreichbarem Server: Hinweis, Knopf wieder frei", async () => {
    useAuthStore.setState({ mfaToken: "mfa" });
    renderLogin(offline);
    fireEvent.change(screen.getByLabelText("Sechsstelliger Code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Server nicht erreichbar – bitte gleich noch einmal versuchen.");
    expect(screen.getByRole("button", { name: "Bestätigen" })).toBeEnabled();
    expect(useAuthStore.getState().mfaToken).toBe("mfa");
  });

  const tooMany = (detail: string) => async (input: RequestInfo | URL) => {
    if (String(input) === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
    return new Response(JSON.stringify({ detail }), { status: 429, headers: { "Retry-After": "240" } });
  };

  it("gesperrte Anmeldung: Hinweis des Servers mit Wartezeit", async () => {
    renderLogin(tooMany("Zu viele Fehlversuche. Bitte in 4 Minuten erneut versuchen."));
    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "geheim" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Zu viele Fehlversuche. Bitte in 4 Minuten erneut versuchen.");
    expect(screen.getByRole("button", { name: "Anmelden" })).toBeEnabled();
  });

  it("zu viele falsche Codes: zurück zum Passwort, Hinweis bleibt stehen", async () => {
    useAuthStore.setState({ mfaToken: "mfa" });
    renderLogin(tooMany("Zu viele falsche 2FA-Codes. Bitte melde dich erneut an."));
    fireEvent.change(screen.getByLabelText("Sechsstelliger Code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Zu viele falsche 2FA-Codes. Bitte melde dich erneut an.");
    expect(useAuthStore.getState().mfaToken).toBeNull();
    expect(screen.getByLabelText("Benutzername")).toBeInTheDocument();
  });

  it("„Passwort vergessen?“ erklärt Wiederherstellungs-Code, Admin-Zurücksetzen und den Notfall-Befehl", () => {
    renderLogin();
    const toggle = screen.getByRole("button", { name: "Passwort vergessen?" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Notfall-Befehl auf dem Server/)).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Handy nicht zur Hand\? Wiederherstellungs-Code verwenden/)).toBeInTheDocument();
    expect(screen.getByText(/Einstellungen → Benutzer → Bearbeiten/)).toBeInTheDocument();
    expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password <benutzername>")).toBeInTheDocument();
    expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin list-users")).toBeInTheDocument();
    expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa <benutzername>")).toBeInTheDocument();
    // Der Link ist kein Absende-Knopf: ohne Eingaben passiert beim Aufklappen nichts.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText(/Notfall-Befehl auf dem Server/)).not.toBeInTheDocument();
  });
});
