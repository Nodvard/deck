import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SshHardening } from "./SshHardening";

const propose = vi.hoisted(() => vi.fn());
vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), proposeAndApprove: propose }));

let state: { has_key: boolean; authorized: boolean; hardened: boolean; managed: boolean; root: boolean; warning: string | null };
let credentials: { id: string; kind: string; username: string; port: number; is_default: boolean }[];

beforeEach(() => {
  state = { has_key: true, authorized: true, hardened: false, managed: false, root: false, warning: null };
  credentials = [{ id: "c1", kind: "ssh_key", username: "deck", port: 22, is_default: true }];
  propose.mockReset().mockResolvedValue({ kind: "ok", text: "Erledigt." });
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn().mockResolvedValue(true), promptDialog: vi.fn(), hasPermission: () => true,
  };
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path.endsWith("/credentials") ? credentials : state))));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("SSH-Härtung", () => {
  it("sperrt Passwortzugänge und führt zum Hinterlegen eines Schlüssels", async () => {
    state.has_key = false;
    state.authorized = false;
    credentials[0].kind = "ssh_password";
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText("Schlüssel hinterlegen")).toHaveAttribute("href", "/settings/hosts");
    expect(screen.getByRole("button", { name: "SSH härten" })).toBeDisabled();
    expect(screen.getByText(/nutzt nur ein Passwort/)).toBeInTheDocument();
  });

  it("trägt einen fehlenden Schlüssel als eigene freigegebene Aktion ein", async () => {
    state.authorized = false;
    credentials = [
      { id: "c-password", kind: "ssh_password", username: "deck", port: 22, is_default: true },
      { id: "c1", kind: "ssh_key", username: "deck", port: 22, is_default: false },
    ];
    render(<SshHardening hostId="h1" canManage />);
    fireEvent.click(await screen.findByRole("button", { name: "Deck-Schlüssel eintragen" }));
    await waitFor(() => expect(propose).toHaveBeenCalledWith(expect.stringContaining("/hosts/h1/ssh-hardening"), { mode: "add-key", credential_id: "c-password", key_credential_id: "c1" }, "high", expect.any(AbortSignal)));
    expect(screen.getByRole("button", { name: "SSH härten" })).toBeDisabled();
  });

  it("sperrt die Eintragung mit dem noch nicht autorisierten Zielschlüssel", async () => {
    state.authorized = false;
    render(<SshHardening hostId="h1" canManage />);
    const addKey = await screen.findByRole("button", { name: "Deck-Schlüssel eintragen" });
    expect(addKey).toBeDisabled();
    expect(screen.getByText(/einen bereits funktionierenden Zugang wählen/)).toBeInTheDocument();
    fireEvent.click(addKey);
    expect(propose).not.toHaveBeenCalled();
  });

  it("wählt Passwortzugang und einzutragenden Schlüssel getrennt", async () => {
    credentials = [
      { id: "c-password", kind: "ssh_password", username: "deck", port: 22, is_default: true },
      { id: "c-old", kind: "ssh_key", username: "deck", port: 22, is_default: false },
      { id: "c-new", kind: "ssh_key", username: "deck", port: 22, is_default: false },
    ];
    state.authorized = false;
    render(<SshHardening hostId="h1" canManage />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Einzutragender Schlüssel" }), { target: { value: "c-new" } });
    fireEvent.click(await screen.findByRole("button", { name: "Deck-Schlüssel eintragen" }));
    await waitFor(() => expect(propose).toHaveBeenCalledWith(expect.any(String), {
      mode: "add-key", credential_id: "c-password", key_credential_id: "c-new",
    }, "high", expect.any(AbortSignal)));
    expect(screen.getByRole("button", { name: "SSH härten" })).toBeDisabled();
    expect(screen.getByText(/als Standard setzen/)).toBeInTheDocument();
  });

  it("verwendet den gewählten Schlüsselzugang auch als Zielschlüssel", async () => {
    credentials = [
      { id: "c-old", kind: "ssh_key", username: "deck", port: 22, is_default: false },
      { id: "c-new", kind: "ssh_key", username: "deck", port: 22, is_default: true },
    ];
    render(<SshHardening hostId="h1" canManage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "SSH härten" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "Einzutragender Schlüssel" })).toHaveValue("c-new");
    fireEvent.change(screen.getByRole("combobox", { name: "Anmelden mit" }), { target: { value: "c-old" } });
    expect(screen.getByRole("combobox", { name: "Einzutragender Schlüssel" })).toHaveValue("c-old");
  });

  it("erlaubt den Passwortzugang bei einem noch abgelehnten Standardschlüssel", async () => {
    credentials = [
      { id: "c-new", kind: "ssh_key", username: "deck", port: 22, is_default: true },
      { id: "c-password", kind: "ssh_password", username: "deck", port: 22, is_default: false },
    ];
    state.authorized = false;
    vi.stubGlobal("fetch", vi.fn(async (path: string) => path.endsWith("/credentials")
      ? new Response(JSON.stringify(credentials))
      : path.includes("credential_id=c-new&")
        ? new Response(JSON.stringify({ detail: "Schlüssel abgelehnt" }), { status: 400 })
        : new Response(JSON.stringify(state))));
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText("Schlüssel abgelehnt")).toBeInTheDocument();
    expect(screen.getByText(/einen bereits funktionierenden Zugang wählen/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deck-Schlüssel eintragen" })).not.toBeInTheDocument();
    expect(propose).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox", { name: "Anmelden mit" }), { target: { value: "c-password" } });
    fireEvent.click(await screen.findByRole("button", { name: "Deck-Schlüssel eintragen" }));
    await waitFor(() => expect(propose).toHaveBeenCalledWith(expect.any(String), {
      mode: "add-key", credential_id: "c-password", key_credential_id: "c-new",
    }, "high", expect.any(AbortSignal)));
  });

  it("warnt bei root vor dem Härten und nutzt das Aktions-Gate", async () => {
    state.root = true;
    state.warning = "Root bleibt mit Schlüssel erlaubt (PermitRootLogin prohibit-password).";
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText(/Warnung: Root/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "SSH härten" }));
    await waitFor(() => expect(propose).toHaveBeenCalledWith(expect.any(String), { mode: "on" }, "high", expect.any(AbortSignal)));
    expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalledWith(expect.stringContaining("prohibit-password"), expect.any(Object));
  });

  it("nennt eingetragene root-Schlüssel, statt root-Sperre anzukündigen", async () => {
    Object.assign(state, { root_keys: true });
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText(/Für root sind Schlüssel eingetragen/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "SSH härten" }));
    await waitFor(() => expect(propose).toHaveBeenCalled());
    const text = vi.mocked(window.__nodvardDeck.confirmDialog).mock.calls[0][0];
    expect(text).toContain("bleibt root mit Schlüssel erlaubt");
    expect(text).toContain("braucht danach einen Schlüssel");
    expect(text).not.toContain("Root-Anmeldungen werden gesperrt");
  });

  it("zeigt den Zustand und bietet die Rücknahme mit Freigabe an", async () => {
    state.hardened = state.managed = true;
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText("SSH: gehärtet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Härtung zurücknehmen" }));
    await waitFor(() => expect(propose).toHaveBeenCalledWith(expect.any(String), { mode: "off", credential_id: "c1", key_credential_id: "c1" }, "high", expect.any(AbortSignal)));
  });

  it("meldet einen nicht lesbaren Zustand und sperrt die Änderung", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Nicht erreichbar")));
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText("Nicht erreichbar")).toBeInTheDocument();
    expect(screen.getByText("SSH: Zustand unbekannt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SSH härten" })).toBeDisabled();
  });

  it("lädt die Zugangsliste nach einem Fehler erneut", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path.endsWith("/credentials") ? credentials : state)))
      .mockRejectedValueOnce(new Error("Zugänge nicht erreichbar")));
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText("Zugänge nicht erreichbar")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "SSH-Zustand prüfen" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "SSH härten" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "Anmelden mit" })).toHaveValue("c1");
  });

  it("zeigt ohne Verwaltungsrecht keine Aktionen", async () => {
    state.hardened = state.managed = true;
    render(<SshHardening hostId="h1" canManage={false} />);
    await screen.findByText("SSH: gehärtet");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("zeigt den Schlüssel-Link auch bei fehlendem Hilfsskript", async () => {
    credentials[0].kind = "ssh_password";
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path.endsWith("/credentials") ? credentials : {
      ...state, has_key: false, authorized: false, hardened: null, error: "Hilfsskript fehlt",
    }))));
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText("Schlüssel hinterlegen")).toBeInTheDocument();
    expect(screen.getByText("Hilfsskript fehlt")).toBeInTheDocument();
    expect(screen.getByText("SSH: Zustand unbekannt")).toBeInTheDocument();
  });

  it("warnt vor dem Aussperren und verlangt einen eigenen Zugang", async () => {
    render(<SshHardening hostId="h1" canManage />);
    fireEvent.click(await screen.findByRole("button", { name: "SSH härten" }));
    await waitFor(() => expect(window.__nodvardDeck.confirmDialog).toHaveBeenCalled());
    const text = vi.mocked(window.__nodvardDeck.confirmDialog).mock.calls[0][0];
    expect(text).toContain("nur noch mit einem Schlüssel");
    expect(text).toContain("Konsolenzugang");
  });

  it("bietet die Härtung bei Zwei-Faktor nicht an und erklärt warum", async () => {
    Object.assign(state, { two_factor: true });
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText(/zweiten Faktor/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SSH härten" })).toBeDisabled();
  });

  it("weist auf eine unbestätigte Härtung hin und bietet das Zurücknehmen an", async () => {
    Object.assign(state, { managed: true, unconfirmed: true });
    render(<SshHardening hostId="h1" canManage />);
    expect(await screen.findByText(/nicht bestätigt/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Härtung zurücknehmen" })).toBeEnabled();
  });
});
