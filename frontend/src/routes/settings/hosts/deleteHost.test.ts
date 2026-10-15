import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HostOut } from "../../../lib/hosts";
import { useAuthStore } from "../../../state/auth";
import { deleteHost } from "./deleteHost";

const dialogs = vi.hoisted(() => ({
  promptDialog: vi.fn(async (..._args: unknown[]) => "pve1" as string | null),
  confirmIdentity: vi.fn(async (..._args: unknown[]) => ({ current_password: "mein-passwort", totp_code: "123456" }) as { current_password: string; totp_code?: string } | null),
}));
vi.mock("../../../state/dialogs", () => ({ ...dialogs, confirmDialog: vi.fn(async () => true) }));

const HOST = { id: "h1", name: "pve1", display_name: "pve1", address: "192.0.2.10" } as unknown as HostOut;
let calls: { method: string; body: unknown }[];

beforeEach(() => {
  calls = [];
  useAuthStore.setState({ accessToken: "tok", status: "authenticated", mfaToken: null });
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method: init?.method ?? "GET", body });
    if (!body?.current_password) {
      return new Response(
        JSON.stringify({ detail: "Mit dem Server werden auch seine gespeicherten Zugänge gelöscht. Bitte das aktuelle Passwort eingeben.", code: "confirm_required", two_factor: true }),
        { status: 403 },
      );
    }
    return new Response(null, { status: 204 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("deleteHost", () => {
  it("fragt bei gespeicherten Zugängen nach Passwort und Code und sendet dann erneut", async () => {
    expect(await deleteHost(HOST)).toEqual({ status: "deleted" });
    expect(dialogs.confirmIdentity).toHaveBeenCalledWith(expect.stringContaining("gespeicherten Zugänge"), true);
    expect(calls).toEqual([
      { method: "DELETE", body: undefined },
      { method: "DELETE", body: { current_password: "mein-passwort", totp_code: "123456" } },
    ]);
  });

  it("bricht man die Rückfrage ab, bleibt der Server", async () => {
    dialogs.confirmIdentity.mockResolvedValueOnce(null);
    expect(await deleteHost(HOST)).toEqual({ status: "cancelled" });
    expect(calls).toHaveLength(1);
  });
});
