import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";
import { DECK_EDITION, useEditionStore } from "../state/edition";
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
  it("benennt das Passwortfeld unabhängig vom Knopf zum Anzeigen", () => {
    renderLogin();
    const password = screen.getByLabelText("Passwort", { exact: true });
    const toggle = screen.getByRole("button", { name: "Passwort anzeigen" });
    expect(password).toHaveAccessibleName("Passwort");
    expect(toggle.closest("label")).toBeNull();
    fireEvent.click(toggle);
    expect(password).toHaveAccessibleName("Passwort");
    expect(screen.getByRole("button", { name: "Passwort verbergen" })).toBe(toggle);
    fireEvent.click(toggle);
    expect(password).toHaveAttribute("type", "password");
  });

  it("verknüpft leere Felder mit einzelnen Meldungen und fokussiert das erste", () => {
    const login = vi.fn();
    useAuthStore.setState({ login });
    renderLogin();
    const username = screen.getByLabelText("Benutzername");
    const password = screen.getByLabelText("Passwort");
    expect(username).not.toHaveAttribute("aria-invalid", "true");
    expect(password).not.toHaveAttribute("aria-invalid", "true");

    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(username).toHaveFocus();
    for (const [field, message] of [[username, "Bitte Benutzername eingeben."], [password, "Bitte Passwort eingeben."]] as const) {
      expect(field).toHaveAttribute("aria-invalid", "true");
      expect(field).toHaveAccessibleDescription(message);
      const alert = document.getElementById(field.getAttribute("aria-describedby")!);
      expect(alert).toHaveAttribute("role", "alert");
      expect(alert?.previousElementSibling?.contains(field)).toBe(true);
    }
    fireEvent.change(username, { target: { value: "   " } });
    expect(username).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(username, { target: { value: "demo" } });
    expect(username).not.toHaveAttribute("aria-invalid", "true");
    expect(username).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByText("Bitte Benutzername eingeben.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(password).toHaveFocus();
    fireEvent.change(password, { target: { value: "geheim" } });
    expect(password).not.toHaveAttribute("aria-invalid", "true");
    expect(password).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  });

  it("markiert bei nur fehlendem Benutzernamen ausschließlich dieses Feld", () => {
    renderLogin();
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "geheim" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(screen.getByLabelText("Benutzername")).toHaveFocus();
    expect(screen.getByLabelText("Benutzername")).toHaveAccessibleDescription("Bitte Benutzername eingeben.");
    expect(screen.getByLabelText("Passwort")).not.toHaveAttribute("aria-invalid", "true");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("verknüpft eine abgelehnte Anmeldung mit beiden Feldern und entfernt den Hinweis beim Korrigieren", async () => {
    useAuthStore.setState({ login: vi.fn().mockResolvedValue({ ok: false, error: "Ungültiger Benutzername oder Passwort." }) });
    renderLogin();
    const username = screen.getByLabelText("Benutzername");
    const password = screen.getByLabelText("Passwort");
    fireEvent.change(username, { target: { value: "demo" } });
    fireEvent.change(password, { target: { value: "geheim" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    const alert = await screen.findByRole("alert");
    for (const field of [username, password]) {
      expect(field).toHaveAttribute("aria-invalid", "true");
      expect(field).toHaveAccessibleDescription("Ungültiger Benutzername oder Passwort.");
      expect(field).toHaveAttribute("aria-describedby", alert.id);
    }
    expect(alert.previousElementSibling?.contains(password)).toBe(true);
    expect(alert.compareDocumentPosition(screen.getByRole("button", { name: "Passwort vergessen?" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.change(password, { target: { value: "anderes Passwort" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(username).not.toHaveAttribute("aria-invalid", "true");
    expect(password).not.toHaveAttribute("aria-invalid", "true");
  });

  it("öffnet die Hilfe als eigenen Bereich unter der Karte und erklärt zuerst den Wiederherstellungs-Code", () => {
    renderLogin();
    const toggle = screen.getByRole("button", { name: "Passwort vergessen?" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAccessibleName("Hilfe schließen");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const help = screen.getByRole("region", { name: "Hilfe zur Anmeldung" });
    expect(toggle).toHaveAttribute("aria-controls", help.id);
    expect(help.closest(".panel")).toBeNull();
    expect(help.closest("form")).toBeNull();
    expect(help).toHaveClass("max-h-72", "overflow-y-auto");
    expect(help).toHaveAttribute("tabindex", "0");
    expect(toggle).toHaveClass("underline", "self-start");
    expect(toggle.querySelector("svg")).toHaveClass("rotate-180");
    expect(screen.getByRole("button", { name: "Anmelden" }).closest(".panel")?.nextElementSibling).toBe(help);
    expect(within(help).getAllByRole("heading")[1]).toHaveTextContent("Handy verloren? Mit Wiederherstellungs-Code anmelden");
    expect(help).toHaveTextContent("Er ersetzt den Code aus der App, nicht das Passwort.");
    fireEvent.click(toggle);
    expect(toggle).toHaveAccessibleName("Passwort vergessen?");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle.querySelector("svg")).not.toHaveClass("rotate-180");
    expect(screen.queryByRole("region", { name: "Hilfe zur Anmeldung" })).not.toBeInTheDocument();
  });

  it("beschreibt die Backups mit einem vollständigen Satz", () => {
    renderLogin();
    expect(screen.getByText("Du siehst, was gesichert ist, und bekommst eine Warnung, bevor der Speicher voll läuft.")).toBeInTheDocument();
    expect(screen.getByTestId("copyright").closest("aside")).toHaveClass("sticky", "top-0", "self-start", "h-screen");
  });

  it("zeigt nur ein Logo vom eigenen Server, bei einer alten fremden Adresse den Anfangsbuchstaben", () => {
    const branding = useBrandingStore.getState().branding!;
    useBrandingStore.setState({ branding: { ...branding, logo_url: "/api/v1/branding/logo" } });
    const own = renderLogin();
    expect(own.container.querySelector("img")?.getAttribute("src")).toBe("/api/v1/branding/logo");
    own.unmount();

    useBrandingStore.setState({ branding: { ...branding, logo_url: "https://cdn.example.org/logo.png" } });
    const foreign = renderLogin();
    expect(foreign.container.querySelector("img")).toBeNull();
    expect(screen.getAllByText("H").length).toBeGreaterThan(0);
  });

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
    expect(screen.getByLabelText("Benutzername")).toHaveAccessibleDescription("Bitte Benutzername eingeben.");
    expect(screen.getByLabelText("Passwort")).toHaveAccessibleDescription("Bitte Passwort eingeben.");
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

  it.each([false, true])("verknüpft Fehler beim zweiten Faktor mit dem Codefeld (Wiederherstellung: %s)", async (recovery) => {
    useAuthStore.setState({ mfaToken: "mfa", submitMfa: vi.fn().mockResolvedValue({ ok: false, error: "Ungültiger Code." }) });
    renderLogin();
    if (recovery) fireEvent.click(screen.getByRole("button", { name: /Wiederherstellungs-Code verwenden/ }));
    const code = screen.getByLabelText(recovery ? "Wiederherstellungs-Code" : "Sechsstelliger Code");
    fireEvent.change(code, { target: { value: recovery ? "ABCDE-FGHJK" : "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    const alert = await screen.findByRole("alert");
    expect(code).toHaveAttribute("aria-invalid", "true");
    expect(code).toHaveAttribute("aria-describedby", alert.id);
    expect(code).toHaveAccessibleDescription("Ungültiger Code.");
    expect(code).toHaveAccessibleName(recovery ? "Wiederherstellungs-Code" : "Sechsstelliger Code");
    fireEvent.change(code, { target: { value: "654321" } });
    expect(code).not.toHaveAttribute("aria-invalid", "true");
    expect(code).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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
    renderLogin(tooMany("Zu viele falsche Zwei-Faktor-Codes. Bitte melde dich erneut an."));
    fireEvent.change(screen.getByLabelText("Sechsstelliger Code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Zu viele falsche Zwei-Faktor-Codes. Bitte melde dich erneut an.");
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
    expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password <benutzername>")).toHaveClass("whitespace-nowrap", "overflow-x-auto");
    expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin list-users")).toBeInTheDocument();
    expect(screen.getByText("docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa <benutzername>")).toBeInTheDocument();
    // Der Link ist kein Absende-Knopf: ohne Eingaben passiert beim Aufklappen nichts.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText(/Notfall-Befehl auf dem Server/)).not.toBeInTheDocument();
  });

  it("„Passwort vergessen?“ führt zum Abschnitt „Ausgesperrt?“ der Anleitung (neuer Tab)", () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "Passwort vergessen?" }));
    const link = within(screen.getByTestId("forgot-guide")).getByRole("link", { name: /Ausgesperrt\? Notfall-Befehle/ });
    expect(link).toHaveAttribute("href", "https://github.com/nodvard/deck/blob/main/docs/11-ERST-EINRICHTUNG.md#73-ausgesperrt-notfall-befehle");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("„Passwort vergessen?“ zeigt auch den Weg ohne Befehlszeile: Konsole des Containers in Portainer, Docker Desktop, Synology", () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "Passwort vergessen?" }));

    const section = within(screen.getByTestId("forgot-no-command-line"));
    expect(section.getByText("Ohne Befehlszeile: die Konsole des Containers")).toBeInTheDocument();
    for (const [name, where] of [["Portainer:", /Exec Console/], ["Docker Desktop:", /Reiter „Exec“/], ["Synology Container Manager:", /Reiter „Terminal“/], ["Unraid:", /„Console“/]] as const) {
      const item = section.getByText(name).closest("li")!;
      expect(item).toHaveTextContent(where);
    }
    // In der Konsole des Containers steht „docker compose exec ...“ schon davor -- dort gilt nur der Rest.
    expect(section.getByText("python -m nodvard_deck.admin reset-password <benutzername>")).toBeInTheDocument();
    // Die Befehle für die Befehlszeile (compose.yml, ohne -f) bleiben unverändert.
    expect(screen.getByText(/Im Ordner mit der Compose-Datei \(compose\.yml\)/)).toBeInTheDocument();
    expect(screen.queryByText(/ -f /)).not.toBeInTheDocument();
  });

  it("Fußzeile: immer „© 2026 Nico Benks · Nodvard Deck“, auch bei eigenem Branding mit anderem Produktnamen", () => {
    renderLogin();
    // Im Test ist der Produktname aus dem Branding ein anderer als „Nodvard Deck“.
    expect(useBrandingStore.getState().branding?.product_name).not.toBe("Nodvard Deck");
    expect(screen.getByTestId("copyright").textContent).toMatch(/^© 2026(–\d{4})? Nico Benks · Nodvard Deck$/);
  });

  it("Fußzeile: auch ohne Branding gleich", () => {
    useBrandingStore.setState({ branding: null });
    renderLogin();
    expect(screen.getByTestId("copyright").textContent).toMatch(/^© 2026(–\d{4})? Nico Benks · Nodvard Deck$/);
  });

  it("Fußzeile steht zusätzlich unter dem Formular, damit sie am Handy und Tablet sichtbar ist (die linke Spalte ist dort versteckt)", () => {
    renderLogin();
    const compact = screen.getByTestId("copyright-compact");
    expect(compact).toHaveClass("login-copyright");
    expect(screen.getByTestId("copyright")).toHaveClass("login-copyright");
    expect(screen.getByRole("button", { name: "Anmelden" })).toHaveClass("login-submit", "text-on-accent");
    expect(compact.textContent).toBe(screen.getByTestId("copyright").textContent);
    expect(compact.className).toContain("lg:hidden");
    expect(screen.getByTestId("copyright").closest("aside")?.className).toContain("hidden");
  });

  it("Anleitung ohne Befehlszeile nennt den Container nicht mit einem Namen, den es so nicht gibt", () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "Passwort vergessen?" }));
    const section = within(screen.getByTestId("forgot-no-command-line"));
    expect(section.getByText(/Container von Nodvard Deck/)).toBeInTheDocument();
    expect(section.getByText(/nodvard-deck-nodvard-deck-1/)).toBeInTheDocument();
    expect(section.getByText("Docker Desktop:").closest("li")).toHaveTextContent(/Gruppe „nodvard-deck“ aufklappen/);
    expect(section.queryByText(/Konsole des Containers „nodvard-deck“/)).not.toBeInTheDocument();
  });

  it("Notfall-Befehl: sagt ausdrücklich, dass du deinen Benutzernamen statt des Platzhalters schreibst", () => {
    renderLogin();
    fireEvent.click(screen.getByRole("button", { name: "Passwort vergessen?" }));
    const section = screen.getByTestId("forgot-no-command-line");
    expect(section).toHaveTextContent(/Statt <benutzername> schreibst du deinen eigenen Benutzernamen\./);
    expect(section).not.toHaveTextContent(/zum Beispiel admin/);
    expect(section).not.toHaveTextContent(/steht statt/);
  });

  it("Nodvard Dock: Notfall-Befehle und Copyright nennen Dock und den Dienst nodvard-dock", () => {
    useEditionStore.setState({ edition: { ...DECK_EDITION, id: "dock", product_name: "Nodvard Dock", service: "nodvard-dock" } });
    try {
      renderLogin();
      fireEvent.click(screen.getByRole("button", { name: "Passwort vergessen?" }));
      expect(screen.getByText("docker compose exec nodvard-dock python -m nodvard_deck.admin reset-password <benutzername>")).toBeInTheDocument();
      expect(screen.getByText("docker compose exec nodvard-dock python -m nodvard_deck.admin disable-2fa <benutzername>")).toBeInTheDocument();
      const section = within(screen.getByTestId("forgot-no-command-line"));
      expect(section.getByText(/nodvard-dock-nodvard-dock-1/)).toBeInTheDocument();
      expect(screen.getByTestId("copyright").textContent).toMatch(/Nico Benks · Nodvard Dock$/);
      expect(document.body.textContent).not.toMatch(/nodvard-deck|Nodvard Deck/);
    } finally {
      useEditionStore.setState({ edition: DECK_EDITION });
    }
  });
});
