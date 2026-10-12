import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { ExtensionPage } from "./ExtensionPage";

// Zwei Seiten, deren Programm sich nicht nutzen lässt: das eine bringt die Seite nicht mit, das andere lässt sich nicht laden.
vi.mock("../lib/catalog", () => ({
  usePages: () => ({
    data: [
      { ext_id: "leer", path: "/seite", component: "LeereSeite" },
      { ext_id: "kaputt", path: "/seite", component: "KaputteSeite" },
    ],
    isLoading: false,
  }),
}));
vi.mock("/api/v1/extensions/leer/frontend/index.js?v=dev", () => ({ LeereSeite: undefined }));
vi.mock("/api/v1/extensions/kaputt/frontend/index.js?v=dev", () => {
  throw new Error("Failed to fetch dynamically imported module");
});

function renderAt(path: string) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input).replace("/api/v1", "");
    if (url === "/extensions") return new Response(JSON.stringify([{ id: "kaputt", name: "Kaputtes Modul", state: "enabled" }]));
    return new Response(JSON.stringify({ detail: "Unbekannt." }), { status: 404 });
  }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/ext/:extId/*" element={<ExtensionPage />} />
          <Route path="/settings/extensions" element={<p>Erweiterungen</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["*"] },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("ExtensionPage: Programm des Moduls lässt sich nicht laden", () => {
  it("sagt in einem Satz, was passiert ist und was man tun kann, die technische Meldung steht klein darunter", async () => {
    renderAt("/ext/kaputt/seite");
    const box = await screen.findByTestId("module-load-error");

    expect(box.textContent).toContain("ließ sich nicht laden");
    expect(box.textContent).toContain("Lade die Seite einmal neu");
    expect(box.textContent).toContain("Einstellungen → Erweiterungen");
    expect(screen.getByRole("link", { name: /Einstellungen → Erweiterungen/ }).getAttribute("href")).toBe("/settings/extensions");
    expect(screen.getByRole("button", { name: "Seite neu laden" })).toBeInTheDocument();
    const technical = screen.getByText(/Technische Meldung:/);
    // Der Text kommt vom Browser (hier von Vitest, das den Fehler der Mock-Fabrik umhüllt): er steht unverändert da.
    expect(technical.textContent).toMatch(/Technische Meldung: \S+/);
    expect(technical.className).toContain("text-xs");
    // der alte Satz ist weg
    expect(document.body.textContent).not.toContain("Fehler im Modul");
  });

  it("nennt das Modul beim Namen, sobald die Liste da ist", async () => {
    renderAt("/ext/kaputt/seite");
    await screen.findByTestId("module-load-error");
    await screen.findByText(/Das Modul „Kaputtes Modul“ ließ sich nicht laden/);
  });

  it("gilt auch, wenn das Programm die Seite nicht mitbringt", async () => {
    renderAt("/ext/leer/seite");
    const box = await screen.findByTestId("module-load-error");
    expect(box.textContent).toContain("Das Modul „leer“ ließ sich nicht laden");
    expect(screen.getByText(/Technische Meldung:/).textContent).toContain('Export "LeereSeite" fehlt im Bundle von "leer"');
  });
});
