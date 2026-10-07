import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { AuditSettings } from "./AuditSettings";

/**
 * Protokolleinträge tragen die Kennung, die zur Zeit des Vorgangs galt. Nach der Umbenennung einer Erweiterung
 * (`legacy_ids`) steht in alten Einträgen die frühere Kennung: sie führt trotzdem zum Namen.
 */
const ENTRY = {
  id: "a1", ts: "2026-10-01T10:00:00Z", actor_type: "extension", actor_id: "nexus-soc", action: "extension.settings",
  target_type: "extension", target_id: "nexus-soc", outcome: "success", reason: null, detail: {}, ip: null, user_agent: null,
};

let extensions: unknown[];

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["audit.read"] },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    if (path === "/audit") return new Response(JSON.stringify([ENTRY]));
    if (path === "/extensions") return new Response(JSON.stringify(extensions));
    throw new Error(`Unerwarteter Fetch: ${path}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderAudit() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
}

describe("Protokoll: Erweiterungen nach einer Umbenennung", () => {
  it("zeigt zur früheren Kennung in „Wer“ und „Ziel“ den Namen der umbenannten Erweiterung", async () => {
    extensions = [{ id: "shield", name: "Nodvard Shield", legacy_ids: ["nexus-soc"], state: "enabled" }];
    renderAudit();

    await waitFor(() => expect(document.body.textContent).toContain("Erweiterung · Nodvard Shield"));
    const cells = [...document.querySelectorAll("tbody tr td")].map((td) => td.textContent);
    expect(cells).toContain("Erweiterung · Nodvard Shield");
    expect(screen.queryByText(/nexus-soc/)).toBeNull();
  });

  it("ohne `legacy_ids` bleibt die Kennung stehen, wenn es zu ihr keine Erweiterung gibt", async () => {
    extensions = [{ id: "shield", name: "Nodvard Shield", state: "enabled" }];
    renderAudit();

    await screen.findByText("Erweiterung eingerichtet");
    await waitFor(() => expect(document.body.textContent).toContain("Erweiterung · nexus-soc"));
    expect(document.body.textContent).not.toContain("Nodvard Shield");
  });
});
