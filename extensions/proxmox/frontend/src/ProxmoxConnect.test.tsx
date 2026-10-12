import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProxmoxConnect } from "./ProxmoxConnect";

const FINGERPRINT = Array.from({ length: 32 }, () => "AB").join(":");
const NEW_FINGERPRINT = Array.from({ length: 32 }, () => "CD").join(":");
const SELF_SIGNED = {
  sha256: FINGERPRINT, trusted: false, subject: "CN=pve1.example", issuer: "CN=pve1.example",
  not_after: "2028-01-31T00:00:00+00:00", alt_names: ["pve1.example"],
};
const CONNECTED = {
  status: "connected",
  connection: { name: "pve1", base_url: "https://192.168.2.10:8006", token_id: "nodvard@pve!deck", tls_insecure_skip_verify: true },
  created: ["Rolle NodvardDeck", "Benutzer nodvard@pve", "API-Token nodvard@pve!deck"],
  nodes: ["pve1"],
  notes: [],
};

type Answer = { status?: number; body: unknown };
type Call = { path: string; body: Record<string, unknown> };

function mockFetch(answers: Record<string, Answer[]>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^\/api\/v1\/ext\/proxmox/, "");
    const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
    calls.push({ path, body });
    const answer = answers[path]?.shift();
    if (!answer) throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
    return new Response(JSON.stringify(answer.body), { status: answer.status ?? 200 });
  }));
  return calls;
}

beforeEach(() => {
  window.__lattice = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn(),
    promptDialog: vi.fn(),
    hasPermission: () => true,
  };
});

async function toLoginStep(): Promise<void> {
  fireEvent.change(screen.getByLabelText("Adresse des Proxmox-Servers"), { target: { value: "192.168.2.10" } });
  fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
  await screen.findByLabelText("Passwort");
}

function typePassword(value: string): HTMLInputElement {
  const field = screen.getByLabelText("Passwort") as HTMLInputElement;
  fireEvent.change(field, { target: { value } });
  return field;
}

describe("ProxmoxConnect", () => {
  it("zeigt erst den Fingerabdruck und schickt das Passwort nur nach Bestätigung, an genau dieses Zertifikat", async () => {
    const calls = mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{ body: CONNECTED }],
    });
    const onConnected = vi.fn();
    render(<ProxmoxConnect onConnected={onConnected} onClose={vi.fn()} />);
    await toLoginStep();

    expect(calls[0]).toEqual({ path: "/connect/certificate", body: { base_url: "192.168.2.10" } });
    expect(screen.getByLabelText("Fingerabdruck (SHA-256)")).toHaveTextContent(FINGERPRINT);
    expect(screen.getByText(/Zertifikate/)).toBeInTheDocument();
    const field = typePassword("geheim");
    const submit = screen.getByRole("button", { name: "Verbinden" });
    expect(submit).toBeDisabled();
    expect(field).toHaveAttribute("autocomplete", "new-password");

    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    // Das Feld ist sofort leer, nicht erst nach der Antwort.
    expect(field.value).toBe("");

    expect(await screen.findByText(/Verbunden als „pve1“/)).toBeInTheDocument();
    expect(calls[1]).toEqual({
      path: "/connect",
      body: {
        base_url: "https://192.168.2.10:8006", username: "root@pam", password: "geheim", name: null,
        fingerprint: FINGERPRINT, access: "full", backups: false,
      },
    });
    expect(screen.getByText("API-Token nodvard@pve!deck")).toBeInTheDocument();
    expect(onConnected).toHaveBeenCalledWith("pve1");
  });

  it("ohne Haken geht nichts hinaus, auch wenn das Formular anders als über den Knopf abgeschickt wird", async () => {
    const calls = mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{ body: CONNECTED }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    typePassword("geheim");
    // Enter im Passwortfeld schickt das Formular ab, auch bei gesperrtem Knopf in manchen Browsern.
    fireEvent.submit(screen.getByLabelText("Passwort").closest("form") as HTMLFormElement);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls.map((c) => c.path)).toEqual(["/connect/certificate"]);
  });

  it("ein anerkanntes Zertifikat braucht keinen Haken, ein Fingerabdruck geht dann nicht mit", async () => {
    const calls = mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://pve.example:8006", certificate: { ...SELF_SIGNED, trusted: true } } }],
      "/connect": [{ body: CONNECTED }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    expect(screen.getByTestId("certificate-trusted")).toBeInTheDocument();
    expect(screen.queryByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein.")).toBeNull();
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Nur ansehen"));
    fireEvent.change(screen.getByLabelText("Kurzname (freiwillig)"), { target: { value: " lab " } });
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));
    await screen.findByTestId("proxmox-connect-done");
    expect(calls[1].body).toMatchObject({ fingerprint: null, access: "read", name: "lab" });
  });

  it("ein geändertes Zertifikat: neuer Fingerabdruck, Haken wieder weg, Fehler steht da", async () => {
    mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{
        status: 409,
        body: {
          detail: "Achtung: Der Server zeigt ein anderes Zertifikat als das, das du bestätigt hast. Dein Passwort wurde nicht gesendet.",
          code: "certificate_changed", certificate: { ...SELF_SIGNED, sha256: NEW_FINGERPRINT },
        },
      }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Dein Passwort wurde nicht gesendet.");
    expect(screen.getByLabelText("Fingerabdruck (SHA-256)")).toHaveTextContent(NEW_FINGERPRINT);
    expect(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein.")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Verbinden" })).toBeDisabled();
  });

  it("ein Zertifikatswechsel nach der Anmeldung: ehrlicher Text und der neue Fingerabdruck zum Vergleichen", async () => {
    mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{
        status: 409,
        body: {
          detail: "Achtung: Der Server zeigt inzwischen ein anderes Zertifikat als das, das du bestätigt hast. Dein Passwort ging vorher nur an den Server mit dem bestätigten Zertifikat.",
          code: "certificate_changed_after_login", certificate: { ...SELF_SIGNED, sha256: NEW_FINGERPRINT },
          created: ["Rolle NodvardDeck"],
        },
      }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("ging vorher nur an den Server mit dem bestätigten Zertifikat");
    expect(alert).not.toHaveTextContent("nicht gesendet");
    expect(screen.getByLabelText("Fingerabdruck (SHA-256)")).toHaveTextContent(NEW_FINGERPRINT);
    expect(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein.")).not.toBeChecked();
  });

  it("voreingestellt ist „Ansehen und steuern“", async () => {
    mockFetch({ "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }] });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    expect(screen.getByRole("radio", { name: /Ansehen und steuern/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Nur ansehen" })).not.toBeChecked();
  });

  it("ohne Modul Proxmox-Backups gibt es keinen Schalter dafür", async () => {
    mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED, backups_available: false } }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    expect(screen.queryByLabelText(/Auch Proxmox-Backups verbinden/)).toBeNull();
  });

  it("mit Modul Proxmox-Backups: Schalter an, Ergebnis und alte Tokens in der Erfolgsmeldung", async () => {
    const calls = mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED, backups_available: true } }],
      "/connect": [{
        body: {
          ...CONNECTED,
          backups: {
            name: "pve1", token_id: "nodvard-backup@pve!deck", replaced_token_id: null, storages: ["nas"], pool: "nodvard-kopien",
            tls_insecure_skip_verify: true, enabled: true,
          },
          old_tokens: [{
            token_id: "nodvard@pve!deck-2", user: "nodvard@pve", name: "deck-2", privsep: true,
            command: "pveum user token remove nodvard@pve deck-2",
          }],
          discovery: "started",
        },
      }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    const toggle = screen.getByLabelText(/Auch Proxmox-Backups verbinden/);
    expect(toggle).toBeChecked();
    // Ehrlich, was Proxmox diesem Zugang erlaubt, und dass Nodvard Deck es nicht tut.
    expect(toggle.closest("label")).toHaveTextContent(
      "Proxmox erlaubt diesem Zugang auch, Sicherungen zu löschen und einen Gast aus einer Sicherung zu überschreiben – Nodvard Deck tut beides nie.",
    );
    expect(toggle.closest("label")).toHaveTextContent("Benutzer nodvard-backup@pve");
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));
    await screen.findByTestId("proxmox-connect-done");
    expect(calls[1].body).toMatchObject({ backups: true, access: "full" });
    expect(screen.getByTestId("proxmox-connect-backups")).toHaveTextContent("Proxmox-Backups ist als „pve1“ verbunden");
    expect(screen.getByText(/liest Knoten, VMs und Container jetzt ein/)).toBeInTheDocument();
    expect(screen.getByLabelText("Befehle zum Löschen")).toHaveTextContent("pveum user token remove nodvard@pve deck-2");
  });

  it("Proxmox-Backups abgewählt: geht als false mit", async () => {
    const calls = mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED, backups_available: true } }],
      "/connect": [{ body: CONNECTED }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    fireEvent.click(screen.getByLabelText(/Auch Proxmox-Backups verbinden/));
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));
    await screen.findByTestId("proxmox-connect-done");
    expect(calls[1].body).toMatchObject({ backups: false });
    expect(screen.queryByTestId("old-tokens")).toBeNull();
  });

  it("Zwei-Faktor: fragt nach dem Code und schickt ihn mit der Vorgangsnummer, nicht noch einmal das Passwort", async () => {
    const calls = mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{ body: { status: "tfa_required", tfa: { flow: "vorgang-1", kinds: ["totp", "recovery"], expires_in_s: 180 } } }],
      "/connect/tfa": [{ body: CONNECTED }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));

    await screen.findByTestId("proxmox-connect-tfa");
    expect(screen.getByRole("combobox")).toHaveValue("totp");
    const codeField = screen.getByLabelText("Code aus der App") as HTMLInputElement;
    expect(codeField).toHaveAttribute("autocomplete", "one-time-code");
    fireEvent.change(codeField, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    expect(codeField.value).toBe("");
    await screen.findByTestId("proxmox-connect-done");
    expect(calls[2]).toEqual({ path: "/connect/tfa", body: { flow: "vorgang-1", code: "123456", kind: "totp" } });
    expect(JSON.stringify(calls[2].body)).not.toContain("geheim");
  });

  it("Zwei-Faktor: falscher Code führt zurück zur Anmeldung mit Hinweis", async () => {
    mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{ body: { status: "tfa_required", tfa: { flow: "vorgang-1", kinds: ["totp"], expires_in_s: 180 } } }],
      "/connect/tfa": [{ status: 400, body: { detail: "Der Code stimmt nicht oder ist abgelaufen.", code: "tfa_wrong" } }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));
    await screen.findByTestId("proxmox-connect-tfa");
    expect(screen.queryByRole("combobox")).toBeNull();
    fireEvent.change(screen.getByLabelText("Code aus der App"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Der Code stimmt nicht");
    await waitFor(() => expect(screen.getByLabelText("Passwort")).toHaveValue(""));
  });

  it("Zwei-Faktor: ein fremdes Geheimnis unter dem Kurznamen führt zurück zur Anmeldung, der Grund steht da", async () => {
    const detail =
      "Im Tresor liegt schon ein Geheimnis „proxmox-token:pve1“, das nicht von Proxmox VE stammt (etwa von Hand angelegt).";
    mockFetch({
      "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }],
      "/connect": [{ body: { status: "tfa_required", tfa: { flow: "vorgang-1", kinds: ["totp"], expires_in_s: 180 } } }],
      "/connect/tfa": [{ status: 409, body: { detail, code: "secret_foreign" } }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    await toLoginStep();
    typePassword("geheim");
    fireEvent.click(screen.getByLabelText("Der Fingerabdruck stimmt mit dem in Proxmox überein."));
    fireEvent.click(screen.getByRole("button", { name: "Verbinden" }));
    await screen.findByTestId("proxmox-connect-tfa");
    fireEvent.change(screen.getByLabelText("Code aus der App"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("proxmox-token:pve1");
    await waitFor(() => expect(screen.queryByTestId("proxmox-connect-tfa")).toBeNull());
    expect(screen.getByLabelText("Passwort")).toHaveValue("");
  });

  it("warnt im Formular, wenn die Seite ohne HTTPS läuft", async () => {
    mockFetch({ "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }] });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} pageLocation={{ protocol: "http:", hostname: "192.168.2.5" }} />);
    await toLoginStep();
    expect(screen.getByTestId("password-in-clear")).toHaveTextContent("Das Passwort geht unverschlüsselt bis zu Nodvard Deck");
  });

  it("über HTTPS keine Warnung", async () => {
    mockFetch({ "/connect/certificate": [{ body: { base_url: "https://192.168.2.10:8006", certificate: SELF_SIGNED } }] });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} pageLocation={{ protocol: "https:", hostname: "deck.example" }} />);
    await toLoginStep();
    expect(screen.queryByTestId("password-in-clear")).toBeNull();
  });

  it("eine http://-Adresse: der Grund vom Server steht da, es gibt keinen Anmeldeschritt", async () => {
    mockFetch({
      "/connect/certificate": [{
        status: 400, body: { detail: "Für diesen Weg braucht Nodvard Deck eine https://-Adresse.", code: "https_required" },
      }],
    });
    render(<ProxmoxConnect onConnected={vi.fn()} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Adresse des Proxmox-Servers"), { target: { value: "http://192.168.2.10:8006" } });
    fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("https://-Adresse");
    expect(screen.queryByLabelText("Passwort")).toBeNull();
  });
});
