import { beforeEach, describe, expect, it, vi } from "vitest";

import { dropConnectionToken } from "./connectionToken";

type Call = { method: string; url: string; body: unknown };

function mockFetch(answers: { status: number; body?: unknown }[]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url: String(input), body: JSON.parse(String(init?.body ?? "null")) });
    const answer = answers.shift();
    if (!answer) throw new Error("Unerwarteter Fetch");
    return new Response(answer.body === undefined ? null : JSON.stringify(answer.body), { status: answer.status });
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

describe("dropConnectionToken", () => {
  it("löscht über den Kern und fragt dort nach dem Passwort", async () => {
    const calls = mockFetch([
      { status: 403, body: { detail: "Bitte Passwort.", code: "confirm_required", two_factor: true } },
      { status: 204 },
    ]);
    window.__nodvardDeck.confirmIdentity = vi.fn().mockResolvedValue({ current_password: "pw", totp_code: "123456" });
    expect(await dropConnectionToken("proxmox", "proxmox-token:pve 1")).toBe(true);
    expect(calls.map((c) => [c.method, c.url])).toEqual([
      ["DELETE", "/api/v1/extensions/proxmox/secrets?label=proxmox-token%3Apve%201"],
      ["DELETE", "/api/v1/extensions/proxmox/secrets?label=proxmox-token%3Apve%201"],
    ]);
    expect(calls[1].body).toEqual({ current_password: "pw", totp_code: "123456" });
  });

  it("abgebrochen: false, kein zweiter Aufruf", async () => {
    const calls = mockFetch([{ status: 403, body: { detail: "Bitte Passwort.", code: "confirm_required" } }]);
    window.__nodvardDeck.confirmIdentity = vi.fn().mockResolvedValue(null);
    expect(await dropConnectionToken("backups", "backups-token:pve1")).toBe(false);
    expect(calls).toHaveLength(1);
  });

  it("meldet einen Fehler des Kerns", async () => {
    mockFetch([{ status: 400, body: { detail: "Das aktuelle Passwort stimmt nicht." } }]);
    await expect(dropConnectionToken("backups", "backups-token:pve1")).rejects.toThrow("stimmt nicht");
  });
});
