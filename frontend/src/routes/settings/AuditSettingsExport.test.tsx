import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { AuditSettings } from "./AuditSettings";

/**
 * Export des Protokolls bei abgelaufener Anmeldung (Tab lange im Hintergrund, Rechner im
 * Ruhezustand): wie `api.get` einmal still erneuern und noch einmal versuchen, statt mit
 * "Nicht authentifiziert" zu scheitern. Das Token "tok" ist hier abgelaufen, erst "tok-neu" gilt.
 */
const USER = { id: "u1", username: "nico", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["audit.read"] };
const UNAVAILABLE = "Server gerade nicht erreichbar – bitte gleich noch einmal versuchen.";

let refreshCalls: number;
let exportAuth: (string | null)[];
let exportUrls: string[];
let savedFiles: string[];

function stubBackend(refresh: () => Response, exportResponse?: (auth: string | null) => Response) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.replace(/^\/api\/v1/, "").split("?")[0];
    const auth = new Headers(init?.headers).get("Authorization");
    if (path === "/auth/refresh") {
      refreshCalls += 1;
      return refresh();
    }
    if (path === "/audit") return new Response("[]");
    if (path === "/audit/export") {
      exportAuth.push(auth);
      exportUrls.push(url);
      if (exportResponse) return exportResponse(auth);
      return auth === "Bearer tok-neu"
        ? new Response('{"action":"login.succeeded"}\n', { status: 200 })
        : new Response(JSON.stringify({ detail: "Nicht authentifiziert." }), { status: 401 });
    }
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
}

const refreshOk = () => new Response(JSON.stringify({ access_token: "tok-neu", user: USER }), { status: 200 });

beforeEach(() => {
  refreshCalls = 0;
  exportAuth = [];
  exportUrls = [];
  savedFiles = [];
  useAuthStore.setState({ accessToken: "tok", user: USER, status: "authenticated", mfaToken: null });
  // jsdom kennt keine Blob-Adressen und folgt keinem Herunterladen-Link.
  const urlStatics = URL as unknown as Record<string, unknown>;
  urlStatics.createObjectURL = () => "blob:test";
  urlStatics.revokeObjectURL = () => {};
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    savedFiles.push(this.download);
  });
});

afterEach(() => {
  const urlStatics = URL as unknown as Record<string, unknown>;
  delete urlStatics.createObjectURL;
  delete urlStatics.revokeObjectURL;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function clickExport() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
  await screen.findByText("Keine Einträge gefunden.");
  fireEvent.click(screen.getByRole("button", { name: /Exportieren/ }));
}

describe("Protokoll exportieren bei abgelaufener Anmeldung", () => {
  it("erste Antwort 401: erneuert einmal und lädt mit dem neuen Token", async () => {
    stubBackend(refreshOk);

    await clickExport();

    await waitFor(() => expect(savedFiles).toHaveLength(1));
    expect(savedFiles[0]).toMatch(/^protokoll-\d{4}-\d{2}-\d{2}\.ndjson$/);
    expect(refreshCalls).toBe(1);
    expect(exportAuth).toEqual(["Bearer tok", "Bearer tok-neu"]);
    expect(screen.queryByText(/Export fehlgeschlagen/)).toBeNull();
  });

  it("Erneuerung abgelehnt: verständliche Meldung statt HTTP 401, kein dritter Versuch", async () => {
    stubBackend(() => new Response(JSON.stringify({ detail: "Abgelaufen" }), { status: 401 }));

    await clickExport();

    expect(await screen.findByText("Export fehlgeschlagen: Nicht authentifiziert.")).toBeInTheDocument();
    expect(exportAuth).toEqual(["Bearer tok"]);
    expect(refreshCalls).toBe(1);
    expect(savedFiles).toEqual([]);
  });

  it("Server beim Erneuern nicht erreichbar: klare Meldung, kein dritter Versuch, weiter angemeldet", async () => {
    stubBackend(() => new Response("Bad Gateway", { status: 502 }));

    await clickExport();

    expect(await screen.findByText(`Export fehlgeschlagen: ${UNAVAILABLE}`)).toBeInTheDocument();
    expect(exportAuth).toEqual(["Bearer tok"]);
    expect(useAuthStore.getState().status).toBe("authenticated");
  });

  it("anderer Fehler: Text des Servers statt HTTP <Status>, ohne Erneuerung", async () => {
    stubBackend(refreshOk, () => new Response(JSON.stringify({ detail: "Dir fehlt das Recht zum Export." }), { status: 403 }));

    await clickExport();

    expect(await screen.findByText("Export fehlgeschlagen: Dir fehlt das Recht zum Export.")).toBeInTheDocument();
    expect(refreshCalls).toBe(0);
    expect(exportAuth).toEqual(["Bearer tok"]);
  });

  it("schickt die Filter weiter, ohne Seitengröße", async () => {
    stubBackend(refreshOk, () => new Response("", { status: 200 }));

    await clickExport();

    await waitFor(() => expect(savedFiles).toHaveLength(1));
    expect(exportUrls[0]).toContain("/api/v1/audit/export?");
    expect(exportUrls[0]).not.toContain("limit=");
    expect(exportUrls[0]).not.toContain("offset=");
  });
});
