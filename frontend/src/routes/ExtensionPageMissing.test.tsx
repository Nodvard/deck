import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { ExtensionPage } from "./ExtensionPage";

// Es gibt keine Seiten: so, als wäre das Modul ausgeschaltet oder die Adresse falsch.
vi.mock("../lib/catalog", () => ({ usePages: () => ({ data: [], isLoading: false }) }));

let extension: { id: string; name: string; state: string; last_error?: string } | null;
let enableCalls: string[];
/** Antwort auf `POST /extensions/<id>/enable`: das Backend setzt den Zustand dabei selbst. */
let enableResult: "enabled" | "error" | "forbidden";

function renderAt(path: string) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace("/api/v1", "");
    if (init?.method === "POST" && extension && url === `/extensions/${extension.id}/enable`) {
      enableCalls.push(url);
      if (enableResult === "forbidden") return new Response(JSON.stringify({ detail: "Dafür fehlt dir das Recht." }), { status: 403 });
      extension = enableResult === "enabled" ? { ...extension, state: "enabled" } : { ...extension, state: "error", last_error: "Start gescheitert" };
      return new Response(JSON.stringify(extension));
    }
    if (url.startsWith("/extensions/") && extension && url === `/extensions/${extension.id}`) return new Response(JSON.stringify(extension));
    return new Response(JSON.stringify({ detail: "Unbekannte Extension." }), { status: 404 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/ext/:extId/*" element={<ExtensionPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function login(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
  });
}

beforeEach(() => {
  extension = null;
  enableCalls = [];
  enableResult = "enabled";
  login(["*"]);
});
afterEach(() => vi.unstubAllGlobals());

describe("ExtensionPage: Seite gibt es nicht", () => {
  it("ausgeschaltetes Modul: sagt das und schaltet es auf Knopfdruck gleich ein", async () => {
    extension = { id: "proxmox", name: "Proxmox VE", state: "disabled" };
    renderAt("/ext/proxmox/nodes");
    const note = await screen.findByTestId("module-off");
    expect(note.textContent).toContain("Das Modul „Proxmox VE“ ist ausgeschaltet");
    expect(note.textContent).toContain("gleich hier einschalten");
    expect(document.body.textContent).not.toContain("keine Berechtigung");
    // Kein Link auf eine Seite ohne Einschalter: der Knopf selbst schaltet ein.
    expect(screen.queryByRole("link", { name: "Einschalten" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Einschalten" }));
    await waitFor(() => expect(enableCalls).toEqual(["/extensions/proxmox/enable"]));
    expect((await screen.findByTestId("module-switched-on")).textContent).toContain("Das Modul „Proxmox VE“ ist eingeschaltet");
  });

  it("Einschalten scheitert (Modul stürzt beim Laden ab): sagt das mit dem Grund", async () => {
    enableResult = "error";
    extension = { id: "proxmox", name: "Proxmox VE", state: "disabled" };
    renderAt("/ext/proxmox/nodes");
    await screen.findByTestId("module-off");
    fireEvent.click(screen.getByRole("button", { name: "Einschalten" }));
    expect((await screen.findByRole("alert")).textContent).toContain("„Proxmox VE“ ließ sich nicht einschalten: Start gescheitert");
    // Auch nachdem der neue Zustand („error“) geladen ist, bleibt der Grund stehen.
    await screen.findByTestId("module-broken");
    expect(screen.getByRole("alert").textContent).toContain("Start gescheitert");
  });

  it("Einschalten abgelehnt: zeigt die Meldung des Servers", async () => {
    enableResult = "forbidden";
    extension = { id: "proxmox", name: "Proxmox VE", state: "disabled" };
    renderAt("/ext/proxmox/nodes");
    await screen.findByTestId("module-off");
    fireEvent.click(screen.getByRole("button", { name: "Einschalten" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Dafür fehlt dir das Recht.");
  });

  it("ohne Recht zum Einschalten: Hinweis auf einen Administrator, kein Knopf", async () => {
    login(["hosts.read"]);
    extension = { id: "proxmox", name: "Proxmox VE", state: "disabled" };
    renderAt("/ext/proxmox/nodes");
    const note = await screen.findByTestId("module-off");
    expect(note.textContent).toContain("Ein Administrator kann es einschalten.");
    expect(screen.queryByRole("button", { name: "Einschalten" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Einschalten" })).toBeNull();
  });

  it("abgestürztes Modul: sagt, dass es nicht läuft", async () => {
    extension = { id: "proxmox", name: "Proxmox VE", state: "error" };
    renderAt("/ext/proxmox/nodes");
    expect((await screen.findByTestId("module-broken")).textContent).toContain("Das Modul „Proxmox VE“ läuft gerade nicht");
  });

  it("unbekannte Adresse oder fehlendes Recht: der bisherige Satz, dazu ein Weg zurück", async () => {
    renderAt("/ext/gibt-es-nicht/seite");
    expect(await screen.findByText(/Seite nicht gefunden oder keine Berechtigung/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zur Übersicht" })).toHaveAttribute("href", "/");
    expect(screen.queryByTestId("module-off")).toBeNull();
  });
});
