import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { hostProvider, providerBadgeText, providerDeleteNote, providerNote } from "../lib/hostProvider";
import type { HostOut } from "../lib/overview";
import { previewState, respond } from "../preview/fixtures";
import { promptDialog } from "../state/dialogs";
import { useAuthStore } from "../state/auth";
import { Cockpit } from "./Cockpit";
import { DeleteHostButton, deletePrompt } from "./settings/hosts/deleteHost";

vi.mock("../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

/**
 * Server, die ein Modul eingelesen hat (hier Proxmox), und das Modul ist aus: Das Cockpit kennzeichnet sie, der
 * Löschen-Dialog verspricht kein „taucht wieder auf“. Läuft das Modul, bleibt alles wie es war.
 */
type Ext = { id: string; state: string; name?: string };

function extensionsWith(proxmoxState: string | null): unknown[] {
  const list = (respond("/api/v1/extensions", "GET") as Ext[]).filter((e) => e.id !== "proxmox");
  return proxmoxState === null ? list : [...list, { ...(respond("/api/v1/extensions", "GET") as Ext[]).find((e) => e.id === "proxmox")!, state: proxmoxState }];
}

function mockFetch(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = path.replace(/^\/api\/v1/, "");
    const body = key in overrides ? overrides[key] : respond(path, init?.method ?? "GET");
    if (body === undefined) throw new Error(`Unerwarteter Fetch: ${path}`);
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

function renderCockpit() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><Cockpit /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
  previewState.noHosts = false;
  previewState.start = false;
  previewState.demo = false;
  vi.mocked(promptDialog).mockClear();
});

describe("hostProvider", () => {
  const host = (provider_ext_id: string | null) => ({ provider_ext_id });
  const list = [
    { id: "proxmox", name: "Proxmox VE", state: "enabled" },
    { id: "gameserver", name: "Gameserver", state: "disabled" },
    { id: "kaputt", name: "Kaputt", state: "error" },
    { id: "neu", name: "Neu", state: "enabled", legacy_ids: ["alt"] },
  ];

  it("kennt Läuft, Aus, Gestört, Fehlt und frühere Kennungen", () => {
    expect(hostProvider(host("proxmox"), list)).toEqual({ name: "Proxmox VE", problem: null });
    expect(hostProvider(host("gameserver"), list)).toEqual({ name: "Gameserver", problem: "disabled" });
    expect(hostProvider(host("kaputt"), list)?.problem).toBe("broken");
    expect(hostProvider(host("weg"), list)).toEqual({ name: "weg", problem: "missing" });
    expect(hostProvider(host("alt"), list)).toEqual({ name: "Neu", problem: null });
  });

  it("behauptet nichts bei von Hand angelegten Servern und solange die Modul-Liste fehlt", () => {
    expect(hostProvider(host(null), list)).toBeUndefined();
    expect(hostProvider(host("proxmox"), undefined)).toBeUndefined();
  });

  it("Texte: Kennzeichen, Tooltip und Löschen-Satz nennen das Modul", () => {
    const off = { name: "Proxmox VE", problem: "disabled" as const };
    expect(providerBadgeText(off)).toBe("Modul aus");
    expect(providerNote(off)).toBe("Eingelesen vom Modul „Proxmox VE“. Das Modul ist ausgeschaltet, darum wird der Zustand dieses Servers nicht mehr aktualisiert.");
    expect(providerDeleteNote(off)).toBe("Das Modul „Proxmox VE“, das diesen Server eingelesen hat, ist aus; der Server kommt nicht wieder, solange es aus bleibt.");
    expect(providerBadgeText({ name: "X", problem: "broken" })).toBe("Modul gestört");
    expect(providerDeleteNote({ name: "X", problem: "broken" })).toContain("läuft gerade nicht; der Server kommt nicht wieder, solange es nicht läuft.");
  });
});

describe("Cockpit: Server eines ausgeschalteten Moduls", () => {
  it("kennzeichnet Knoten, Karten und Zeilen mit „Modul aus“ und erklärt es im Tooltip", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/extensions": extensionsWith("disabled") }));
    renderCockpit();
    const node = await screen.findByTestId("node-n-pve2-provider-off");
    expect(node.textContent).toBe("Modul aus");
    expect(node.getAttribute("title")).toContain("Eingelesen vom Modul „");
    expect(node.getAttribute("title")).toContain("nicht mehr aktualisiert");
    expect(await screen.findByTestId("machine-g-valheim-provider-off")).toHaveTextContent("Modul aus");
    // von Hand angelegt: nichts
    const pi = await screen.findByTestId("server-h-pi");
    expect(within(pi).queryByText("Modul aus")).toBeNull();
  });

  it("auch an der Karte eines eingelesenen Servers mit Messwerten", async () => {
    const latest = { cpu: 5, mem: 40, disk: 30, mem_used_bytes: 1, mem_total_bytes: 2, disk_used_bytes: 1, disk_total_bytes: 3, uptime_s: 100, at: new Date().toISOString(), age_s: 5, stale: false };
    vi.stubGlobal("fetch", mockFetch({ "/extensions": extensionsWith("disabled"), "/hosts/metrics/latest": { hosts: { "g-docker": latest, "h-pi": latest }, stale_after_s: 120 } }));
    renderCockpit();
    expect(await screen.findByTestId("server-g-docker-provider-off")).toHaveTextContent("Modul aus");
    expect(screen.queryByTestId("server-h-pi-provider-off")).toBeNull();
  });

  it("läuft das Modul, steht nirgends ein Kennzeichen", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/extensions": extensionsWith("enabled") }));
    renderCockpit();
    await screen.findByTestId("node-n-pve2");
    await screen.findByTestId("machine-g-valheim");
    expect(screen.queryByText("Modul aus")).toBeNull();
    expect(screen.queryByText("Modul gestört")).toBeNull();
  });

  it("ohne Modul-Liste (Abruf scheitert) behauptet das Cockpit nichts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (path === "/api/v1/extensions") return new Response(JSON.stringify({ detail: "kaputt" }), { status: 500 });
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET")));
    }));
    renderCockpit();
    await screen.findByTestId("node-n-pve2");
    await screen.findByTestId("machine-g-valheim");
    expect(screen.queryByText("Modul aus")).toBeNull();
  });
});

describe("Server löschen: Satz zum Wiederauftauchen", () => {
  const host = { id: "h1", name: "vm-alt", display_name: "vm alt", provider_ext_id: "proxmox" } as HostOut;

  it("läuft das Modul (oder ist es unbekannt), taucht der Server beim nächsten Abgleich wieder auf", () => {
    expect(deletePrompt(host, { name: "Proxmox VE", problem: null })).toContain("taucht beim nächsten Abgleich wieder auf – dann ohne SSH-Zugang.");
    expect(deletePrompt(host)).toContain("taucht beim nächsten Abgleich wieder auf");
  });

  it("ist das Modul aus, kommt er nicht wieder, solange es aus bleibt", () => {
    const text = deletePrompt(host, { name: "Proxmox VE", problem: "disabled" });
    expect(text).toContain("Das Modul „Proxmox VE“, das diesen Server eingelesen hat, ist aus; der Server kommt nicht wieder, solange es aus bleibt.");
    expect(text).not.toContain("taucht");
    expect(text).not.toContain("wieder auf");
  });

  it("von Hand angelegt: kein Zusatz", () => {
    const text = deletePrompt({ ...host, provider_ext_id: null });
    expect(text).not.toContain("Modul");
    expect(text).not.toContain("eingelesen");
  });

  async function openDialog(extensions: unknown) {
    vi.stubGlobal("fetch", mockFetch({ "/extensions": extensions }));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DeleteHostButton host={host} onDeleted={() => {}} onMessage={() => {}} />
      </QueryClientProvider>,
    );
    // Der Dialog nimmt die Modul-Liste, sobald sie da ist; vorher gilt der Normalfall.
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some((c) => String(c[0]).endsWith("/extensions"))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    fireEvent.click(screen.getByRole("button", { name: "Server löschen" }));
    await waitFor(() => expect(promptDialog).toHaveBeenCalled());
    return String(vi.mocked(promptDialog).mock.calls[0][0]);
  }

  it("DeleteHostButton: Modul aus -> neuer Satz", async () => {
    const text = await openDialog(extensionsWith("disabled"));
    expect(text).toContain("ist aus; der Server kommt nicht wieder, solange es aus bleibt.");
    expect(text).not.toContain("taucht");
  });

  it("DeleteHostButton: Modul an -> alter Satz", async () => {
    const text = await openDialog(extensionsWith("enabled"));
    expect(text).toContain("taucht beim nächsten Abgleich wieder auf");
  });
});
