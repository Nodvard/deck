import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { ExtensionPage } from "./ExtensionPage";

/**
 * Alte Adressen einer umbenannten Erweiterung (`legacy_ids`): `/ext/<alt>/…` führt auf `/ext/<neu>/…`,
 * mit Abfrage und Anker. Lesezeichen, ntfy-Links und gespeicherte Meldungen tragen die alte Kennung weiter.
 */
const state = vi.hoisted(() => ({ pages: [] as unknown[] }));
vi.mock("../lib/catalog", () => ({ usePages: () => ({ data: state.pages, isLoading: false }) }));
// Das Bundle der Erweiterung unter der heutigen Kennung (ExtensionPage lädt es per import(url)).
vi.mock("/api/v1/extensions/shield/frontend/index.js?v=dev", () => ({ SocPage: () => <p data-testid="soc-page">Shield-Seite</p> }));
vi.mock("/api/v1/extensions/nexus-soc/frontend/index.js?v=dev", () => ({ SocPage: () => <p data-testid="old-page">Seite der alten Erweiterung</p> }));

const SHIELD_PAGE = { ext_id: "shield", legacy_ext_ids: ["nexus-soc"], id: "soc", path: "/soc", component: "SocPage" };

function renderAt(entries: string[], initialIndex = entries.length - 1) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Unbekannte Extension." }), { status: 404 })));
  const router = createMemoryRouter(
    [
      { path: "/ext/:extId/*", element: <ExtensionPage /> },
      { path: "*", element: <p>Anderswo</p> },
    ],
    { initialEntries: entries, initialIndex },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

const where = (router: ReturnType<typeof renderAt>) => router.state.location.pathname + router.state.location.search + router.state.location.hash;

beforeEach(() => {
  state.pages = [SHIELD_PAGE];
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("ExtensionPage: alte Adresse einer umbenannten Erweiterung", () => {
  it("leitet /ext/<alt>/soc?tab=…#… auf /ext/<neu>/soc?tab=…#… um und zeigt die Seite", async () => {
    const router = renderAt(["/ext/nexus-soc/soc?tab=guard&host=h-pi#oben"]);

    expect(await screen.findByTestId("soc-page")).toBeInTheDocument();
    expect(where(router)).toBe("/ext/shield/soc?tab=guard&host=h-pi#oben");
    expect(screen.queryByTestId("old-page")).toBeNull();
  });

  it("ersetzt die alte Adresse im Verlauf: „Zurück“ führt nicht auf sie zurück", async () => {
    const router = renderAt(["/start", "/ext/nexus-soc/soc?tab=x#y"]);
    await screen.findByTestId("soc-page");

    expect(router.state.historyAction).toBe("REPLACE");
    await router.navigate(-1);
    expect(where(router)).toBe("/start");
  });

  it("ohne Abfrage und Anker bleibt die Adresse schlicht", async () => {
    const router = renderAt(["/ext/nexus-soc/soc"]);
    await screen.findByTestId("soc-page");
    expect(where(router)).toBe("/ext/shield/soc");
  });

  it("ohne `legacy_ext_ids` (keine Umbenennung, älteres Backend) wird nichts umgeleitet", async () => {
    state.pages = [{ ext_id: "shield", id: "soc", path: "/soc", component: "SocPage" }];
    const router = renderAt(["/ext/nexus-soc/soc?tab=guard"]);

    expect(await screen.findByText(/Seite nicht gefunden/)).toBeInTheDocument();
    expect(where(router)).toBe("/ext/nexus-soc/soc?tab=guard");
    expect(screen.queryByTestId("soc-page")).toBeNull();
  });

  it("eine Seite unter der Kennung aus der Adresse geht vor: eine frühere Kennung kann einer anderen Erweiterung gehören", async () => {
    state.pages = [SHIELD_PAGE, { ext_id: "nexus-soc", id: "soc", path: "/soc", component: "SocPage" }];
    const router = renderAt(["/ext/nexus-soc/soc"]);

    expect(await screen.findByTestId("old-page")).toBeInTheDocument();
    expect(where(router)).toBe("/ext/nexus-soc/soc");
  });

  it("leitet nur um, wenn es unter der heutigen Kennung genau diese Seite gibt", async () => {
    const router = renderAt(["/ext/nexus-soc/gibt-es-nicht"]);
    expect(await screen.findByText(/Seite nicht gefunden/)).toBeInTheDocument();
    expect(where(router)).toBe("/ext/nexus-soc/gibt-es-nicht");
  });

  it("eine Kennung, die nicht in `legacy_ext_ids` steht, wird nicht umgeleitet", async () => {
    const router = renderAt(["/ext/irgendwer/soc"]);
    expect(await screen.findByText(/Seite nicht gefunden/)).toBeInTheDocument();
    await waitFor(() => expect(where(router)).toBe("/ext/irgendwer/soc"));
  });
});
