import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CertificateStatus, ConnectionCertificate } from "./certificate";

const OLD = Array.from({ length: 32 }, () => "AB").join(":");
const NEW = Array.from({ length: 32 }, () => "CD").join(":");
const NEWER = Array.from({ length: 32 }, () => "EF").join(":");
const PATH = "/api/v1/ext/proxmox/connections/pve1/certificate";

type Answer = { status?: number; body: unknown };
type Call = { method: string; url: string; body: Record<string, unknown> };

function mockFetch(answers: Answer[]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ method: init?.method ?? "GET", url, body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown> });
    const answer = answers.shift();
    if (!answer) throw new Error(`Unerwarteter Fetch in diesem Test: ${url}`);
    return new Response(JSON.stringify(answer.body), { status: answer.status ?? 200 });
  }));
  return calls;
}

beforeEach(() => {
  window.__nodvardDeck = {
    React: undefined as never,
    ReactDOM: undefined as never,
    ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok",
    confirmDialog: vi.fn(),
    promptDialog: vi.fn(),
    hasPermission: () => true,
  };
});

describe("CertificateStatus", () => {
  it("nennt gemerkt, ungültig, nicht geprüft und normal", () => {
    const { rerender } = render(<CertificateStatus connection={{ tls_insecure_skip_verify: true, tls_fingerprint_sha256: OLD }} />);
    expect(screen.getByText("Fingerabdruck gemerkt")).toBeInTheDocument();
    rerender(<CertificateStatus connection={{ tls_insecure_skip_verify: false, tls_fingerprint_invalid: true }} />);
    expect(screen.getByText("Fingerabdruck ungültig")).toBeInTheDocument();
    rerender(<CertificateStatus connection={{ tls_insecure_skip_verify: true }} />);
    expect(screen.getByText("wird nicht geprüft")).toBeInTheDocument();
    rerender(<CertificateStatus connection={{ tls_insecure_skip_verify: false }} />);
    expect(screen.getByText("normal geprüft")).toBeInTheDocument();
  });
});

describe("ConnectionCertificate", () => {
  it("zeigt nach einem Wechsel den neuen Fingerabdruck und speichert erst nach dem Haken", async () => {
    const onSaved = vi.fn();
    const calls = mockFetch([
      { body: { sha256: NEW, trusted: false, stored: OLD, stored_invalid: false, matches: false, subject: "CN=pve1" } },
      { body: { sha256: NEW, trusted: false, stored: NEW, stored_invalid: false, matches: true } },
    ]);
    render(<ConnectionCertificate extId="proxmox" connection="pve1" onSaved={onSaved} />);

    await screen.findByTestId("certificate-changed");
    expect(screen.getByLabelText("Fingerabdruck des Servers")).toHaveTextContent(NEW);
    expect(screen.getByLabelText("Bisher gemerkt")).toHaveTextContent(OLD);
    expect(screen.getByText(/System → Zertifikate/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "Neuen Fingerabdruck bestätigen" });
    expect(confirm).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: /stimmt mit dem in Proxmox überein/ }));
    fireEvent.click(confirm);

    await screen.findByTestId("certificate-matches");
    expect(calls.map((c) => [c.method, c.url])).toEqual([["GET", PATH], ["POST", PATH]]);
    expect(calls[1].body).toEqual({ sha256: NEW });
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Gemerkt.");
  });

  it("bietet bei einer ungeprüften Verbindung „Fingerabdruck merken“ an", async () => {
    mockFetch([{ body: { sha256: OLD, trusted: false, stored: null, stored_invalid: false, matches: false } }]);
    render(<ConnectionCertificate extId="backups" connection="pve1" unchecked />);
    expect(await screen.findByText(/wird bisher nicht geprüft/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fingerabdruck merken" })).toBeDisabled();
    expect(screen.queryByTestId("certificate-changed")).not.toBeInTheDocument();
  });

  it("zeigt bei 409 den Wert, den der Server jetzt zeigt, und verlangt einen neuen Vergleich", async () => {
    const onSaved = vi.fn();
    const calls = mockFetch([
      { body: { sha256: NEW, trusted: false, stored: OLD, stored_invalid: false, matches: false, subject: "CN=alt", not_after: "2030-01-01T00:00:00Z" } },
      { status: 409, body: { detail: "Der Server zeigt gerade ein anderes Zertifikat.", code: "certificate_mismatch", sha256: NEWER } },
    ]);
    render(<ConnectionCertificate extId="proxmox" connection="pve1" onSaved={onSaved} />);
    await screen.findByTestId("certificate-changed");
    fireEvent.click(screen.getByRole("checkbox", { name: /stimmt mit dem in Proxmox überein/ }));
    fireEvent.click(screen.getByRole("button", { name: "Neuen Fingerabdruck bestätigen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("anderes Zertifikat");
    expect(screen.getByLabelText("Fingerabdruck des Servers")).toHaveTextContent(NEWER);
    expect(screen.queryByText(/CN=alt/)).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /stimmt mit dem in Proxmox überein/ })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Neuen Fingerabdruck bestätigen" })).toBeDisabled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(calls).toHaveLength(2);
  });

  it("sagt bei einer streng prüfenden Verbindung nicht „wird nicht geprüft“", async () => {
    mockFetch([{ body: { sha256: OLD, trusted: false, stored: null, stored_invalid: false, matches: false } }]);
    render(<ConnectionCertificate extId="proxmox" connection="pve1" />);
    expect(await screen.findByText(/klappt die Verbindung noch nicht/)).toBeInTheDocument();
    expect(screen.queryByText(/wird bisher nicht geprüft/)).not.toBeInTheDocument();
  });

  it("meldet, wenn der Server nicht erreichbar ist", async () => {
    mockFetch([{ status: 502, body: { detail: "Der Server ist nicht erreichbar." } }]);
    render(<ConnectionCertificate extId="proxmox" connection="pve1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("nicht erreichbar");
    await waitFor(() => expect(screen.queryByRole("button")).not.toBeInTheDocument());
  });
});
