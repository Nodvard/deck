import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalDialogs } from "../../components/GlobalDialogs";
import { useAuthStore } from "../../state/auth";
import { ExtensionsSettings, providedHostsText } from "./ExtensionsSettings";

/**
 * Ein Modul ausschalten, das Server eingelesen hat: Die Rückfrage nennt die Zahl, sagt, dass die Server stehen
 * bleiben, und wo man sie löscht. Ohne eingelesene Server (oder ohne Recht auf die Serverliste) fragt sie wie sonst.
 */
const EXT = {
  id: "proxmox", version: "1.0.0", state: "enabled", source: "bundled", name: "Proxmox VE", description: null, icon: null,
  granted_permissions: [], last_error: null,
};

let hosts: unknown;
let disabled: number;

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
  disabled = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    if (method === "GET" && path === "/extensions") return new Response(JSON.stringify([EXT]));
    if (method === "GET" && path === "/hosts") {
      if (hosts === "forbidden") return new Response(JSON.stringify({ detail: "Dafür fehlt dir das Recht." }), { status: 403 });
      return new Response(JSON.stringify(hosts));
    }
    if (method === "POST" && path === "/extensions/proxmox/disable") { disabled += 1; return new Response(JSON.stringify({ id: "proxmox", state: "disabled" })); }
    throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
  }));
});

// Eine offen gelassene Rückfrage würde im nächsten Test die Seite für die Suche nach Rollen verbergen.
afterEach(async () => {
  const cancel = screen.queryByRole("button", { name: "Abbrechen" });
  if (cancel) fireEvent.click(cancel);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

const host = (id: string, provider_ext_id: string | null) => ({ id, name: id, display_name: id, provider_ext_id });

async function askToSwitchOff(): Promise<string> {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><ExtensionsSettings /><GlobalDialogs /></MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("switch", { name: "Proxmox VE ausschalten" }));
  const dialog = await screen.findByRole("dialog");
  return dialog.textContent ?? "";
}

describe("Modul ausschalten: eingelesene Server", () => {
  it("nennt die Zahl der eingelesenen Server und sagt, dass sie stehen bleiben und wo man sie löscht", async () => {
    hosts = [host("a", "proxmox"), host("b", "proxmox"), host("c", "proxmox"), host("pi", null), host("x", "gameserver")];
    const text = await askToSwitchOff();
    expect(text).toContain("„Proxmox VE“ ausschalten?");
    expect(text).toContain("Die 3 Server, die es eingelesen hat, bleiben in der Liste stehen, werden aber nicht mehr aktualisiert.");
    expect(text).toContain("löschst du sie unter Einstellungen → Server & Zugänge.");
    // ausschalten geht danach wie bisher
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
    await waitFor(() => expect(disabled).toBe(1));
  });

  it("ein einzelner Server: Einzahl", async () => {
    hosts = [host("a", "proxmox"), host("pi", null)];
    const text = await askToSwitchOff();
    expect(text).toContain("Ein Server, den es eingelesen hat, bleibt in der Liste stehen, wird aber nicht mehr aktualisiert.");
    expect(text).toContain("löschst du ihn unter Einstellungen → Server & Zugänge.");
  });

  it("zählt auch Server, die unter der früheren Kennung des Moduls eingelesen wurden", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
      if (path === "/extensions") return new Response(JSON.stringify([{ ...EXT, legacy_ids: ["pve-alt"] }]));
      if (path === "/hosts") return new Response(JSON.stringify([host("a", "pve-alt"), host("b", "proxmox")]));
      throw new Error(`Unerwarteter Fetch: ${init?.method ?? "GET"} ${path}`);
    }));
    expect(await askToSwitchOff()).toContain("Die 2 Server, die es eingelesen hat");
  });

  it("ohne eingelesene Server fragt sie wie bisher", async () => {
    hosts = [host("pi", null)];
    const text = await askToSwitchOff();
    expect(text).toContain("seine Daten bleiben erhalten.");
    expect(text).not.toContain("eingelesen");
    expect(text).not.toContain("Server & Zugänge");
  });

  it("ohne Recht auf die Serverliste fragt sie wie bisher und lässt sich bestätigen", async () => {
    hosts = "forbidden";
    const text = await askToSwitchOff();
    expect(text).not.toContain("eingelesen");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Ausschalten" }));
    await waitFor(() => expect(disabled).toBe(1));
  });

  it("providedHostsText: nichts bei 0 und bei unbekannter Zahl", () => {
    expect(providedHostsText(0)).toBe("");
    expect(providedHostsText(null)).toBe("");
  });
});
