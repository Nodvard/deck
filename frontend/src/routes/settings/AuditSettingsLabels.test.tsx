import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { AuditSettings, auditActionLabel } from "./AuditSettings";

/**
 * Das Protokoll zeigt zu jedem Vorgang einen deutschen Text; den technischen Namen gibt es nur im Tooltip und in den
 * Einzelheiten. Dass jeder Vorgang des Codes eine Beschriftung hat, prüft `backend/tests/test_audit_labels.py`.
 */
function entry(id: string, action: string, extra: Record<string, unknown> = {}) {
  return {
    id, ts: "2026-10-01T10:00:00Z", actor_type: "user", actor_id: "u1", action, target_type: null, target_id: null,
    outcome: "success", reason: null, detail: {}, ip: null, user_agent: null, ...extra,
  };
}

let entries: unknown[];

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: false, locale: "de", permissions: ["audit.read"] },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    if (path === "/audit") return new Response(JSON.stringify(entries));
    if (path === "/extensions") return new Response("[]");
    throw new Error(`Unerwarteter Fetch: ${path}`);
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderAudit() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuditSettings /></MemoryRouter></QueryClientProvider>);
}

describe("auditActionLabel", () => {
  it("kennt die Vorgänge der Wiederherstellung, des Updates und der Beispieldaten", () => {
    expect(auditActionLabel("system.restore.applied")).toBe("Wiederherstellung durchgeführt");
    expect(auditActionLabel("system.update.requested")).toBe("Update angefordert");
    expect(auditActionLabel("system.update.rollback_requested")).toBe("Rückweg auf die vorige Version angefordert");
    expect(auditActionLabel("system.restart.requested")).toBe("Neustart von Nodvard Deck angefordert");
    expect(auditActionLabel("system.demo.seeded")).toBe("Beispieldaten angelegt");
    expect(auditActionLabel("extension.secret_removed")).toBe("Zugangsdaten gelöscht");
    expect(auditActionLabel("service_matrix.image_update")).toBe("Container-Image aktualisiert");
  });

  it("kennt die Vorgänge von Nodvard Shield", () => {
    expect(auditActionLabel("shield.quarantine")).toBe("Datei in Quarantäne verschoben");
    expect(auditActionLabel("shield.guard_new_port")).toBe("Neuer offener Port bemerkt");
  });

  it("liefert für Unbekanntes nichts (die Tabelle zeigt dann den Namen)", () => {
    expect(auditActionLabel("gibt.es.nicht")).toBeUndefined();
  });
});

describe("Protokoll-Tabelle", () => {
  it("zeigt statt des technischen Namens den deutschen Text, der Name steht im Tooltip", async () => {
    entries = [
      entry("a1", "system.restore.scheduled", { target_type: "restore" }),
      entry("a2", "shield.malware_found"),
      entry("a3", "terminal.open", { target_type: "host", target_id: "h1" }),
      entry("a4", "files.upload", { target_type: "file_source", target_id: "ssh:h1" }),
    ];
    renderAudit();

    const row = (await screen.findByText("Wiederherstellung vorgemerkt")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Wiederherstellung vorgemerkt").getAttribute("title")).toBe("system.restore.scheduled");
    expect(screen.getByText("Schadsoftware gefunden")).toBeInTheDocument();
    expect(screen.getByText("Terminal geöffnet")).toBeInTheDocument();
    // Ziel-Arten ebenfalls in Worten
    await waitFor(() => expect(screen.getByText("Dateiquelle · ssh:h1")).toBeInTheDocument());
    const names = [...document.querySelectorAll("tbody tr td:nth-child(3)")].map((td) => td.textContent);
    expect(names.filter((t) => /^[a-z_]+\.[a-z_.0-9]+$/.test(t ?? ""))).toEqual([]);
  });
});
