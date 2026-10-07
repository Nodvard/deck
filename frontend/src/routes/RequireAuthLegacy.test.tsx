import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { ExtensionPage } from "./ExtensionPage";
import { LoginPage } from "./LoginPage";
import { RequireAuth } from "./RequireAuth";

// ExtensionPage liest die Seiten aus dem Katalog und lädt das Bundle der Erweiterung.
const catalog = vi.hoisted(() => ({ pages: [] as unknown[] }));
vi.mock("../lib/catalog", () => ({ usePages: () => ({ data: catalog.pages, isLoading: false }) }));
vi.mock("/api/v1/extensions/shield/frontend/index.js?v=dev", () => ({ SocPage: () => <p data-testid="soc-page">Shield-Seite</p> }));

const USER = { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] };
const { login: realLogin, submitMfa: realSubmitMfa } = useAuthStore.getState();

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search + location.hash}</p>;
}

function stubServer({ mfa }: { mfa: boolean }) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/v1/auth/bootstrap") return new Response(JSON.stringify({ needed: false }), { status: 200 });
    if (url === "/api/v1/auth/login" && mfa) return new Response(JSON.stringify({ mfa_token: "mfa" }), { status: 202 });
    if (url === "/api/v1/auth/login" || url === "/api/v1/auth/mfa") {
      return new Response(JSON.stringify({ access_token: "tok", expires_in: 900, user: USER }), { status: 200 });
    }
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
}

function signIn() {
  fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "nico" } });
  fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "geheim" } });
  fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: null, login: realLogin, submitMfa: realSubmitMfa });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * Ein ntfy-Link oder Lesezeichen mit der alten Kennung einer umbenannten Erweiterung: nach der Anmeldung führt der
 * Rücksprung auf die alte Adresse, und die Weiterleitung der Erweiterungsseite bringt sie auf die heutige.
 */
describe("RequireAuth -- alter Pfad einer umbenannten Erweiterung", () => {
  function renderOldLink(entry: string) {
    return render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/ext/:extId/*" element={<><Where /><ExtensionPage /></>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  }

  beforeEach(() => {
    catalog.pages = [{ ext_id: "shield", legacy_ext_ids: ["nexus-soc"], id: "soc", path: "/soc", component: "SocPage" }];
  });

  it("nach der Anmeldung landet man mit Reiter, Filter und Anker auf der Seite unter der heutigen Kennung", async () => {
    stubServer({ mfa: false });
    renderOldLink("/ext/nexus-soc/soc?tab=guard&host=h-pi#oben");
    signIn();

    expect(await screen.findByTestId("soc-page")).toBeInTheDocument();
    expect(screen.getByTestId("where").textContent).toBe("/ext/shield/soc?tab=guard&host=h-pi#oben");
  });

  it("auch mit zweitem Faktor", async () => {
    stubServer({ mfa: true });
    renderOldLink("/ext/nexus-soc/soc?tab=quarantine");
    signIn();
    fireEvent.change(await screen.findByLabelText("Sechsstelliger Code"), { target: { value: "123 456" } });
    fireEvent.click(screen.getByRole("button", { name: "Bestätigen" }));

    expect(await screen.findByTestId("soc-page")).toBeInTheDocument();
    expect(screen.getByTestId("where").textContent).toBe("/ext/shield/soc?tab=quarantine");
  });
});
