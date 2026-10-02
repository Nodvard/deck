import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HostOut } from "../../../lib/hosts";
import { checkFixture, hostFixture, mockHostsApi } from "../../../test/hostsMock";
import { AccessCard } from "./AccessCard";

const KEY = { id: "c1", kind: "ssh_key" as const, username: "nodvard", port: 22 };

function renderCard(over: Record<string, unknown>, routes: Record<string, unknown> = {}) {
  mockHostsApi({ "GET /hosts": [], ...routes });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const host = hostFixture({ credential: KEY, ...over }) as unknown as HostOut;
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><AccessCard host={host} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Zugangs-Karte: Abzeichen und Hinweis", () => {
  it("Server antwortet, Anmeldung nie belegt: nicht grün, Hinweis auf „Verbindung prüfen“", () => {
    renderCard({ status: "up", last_seen_at: "2026-10-01T10:00:00Z" });
    expect(screen.getByText("Schlüssel · nodvard").className).not.toContain("emerald");
    expect(screen.getByText("Anmeldung noch nicht bestätigt")).toBeInTheDocument();
    expect(screen.getByTestId("access-hint").textContent).toContain("Einrichtungsbefehl");
  });

  it("Anmeldung belegt: grünes Abzeichen, kein Hinweis", () => {
    renderCard({ status: "up", last_seen_at: "2026-10-01T10:00:00Z", login_ok_at: "2026-10-01T10:00:00Z" });
    expect(screen.getByText("Schlüssel · nodvard").className).toContain("emerald");
    expect(screen.queryByText("noch keine Verbindung")).toBeNull();
    expect(screen.queryByTestId("access-hint")).toBeNull();
  });

  it("Server hat nie geantwortet: Abzeichen nicht grün, gelber Hinweis „noch keine Verbindung“", () => {
    renderCard({ status: "down", last_seen_at: null });
    expect(screen.getByText("Schlüssel · nodvard").className).not.toContain("emerald");
    expect(screen.getByText("noch keine Verbindung")).toBeInTheDocument();
    expect(screen.getByTestId("access-hint").textContent).toContain("Prüfe zuerst den Zugang");
  });

  it("noch nie geprüft: „noch nicht geprüft“ statt Grün", () => {
    renderCard({ status: "unknown", last_seen_at: null });
    expect(screen.getByText("Schlüssel · nodvard").className).not.toContain("emerald");
    expect(screen.getByText("noch nicht geprüft")).toBeInTheDocument();
    expect(screen.getByTestId("access-hint").textContent).toContain("Noch nicht geprüft");
  });

  it("früher erreichbar, jetzt nicht: „keine Antwort“", () => {
    renderCard({ status: "down", last_seen_at: "2026-10-01T09:00:00Z" });
    expect(screen.getByText("keine Antwort")).toBeInTheDocument();
    expect(screen.getByTestId("access-hint").textContent).toContain("antwortet gerade nicht");
  });

  it("Zugriffstoken (kein SSH) bleibt unverändert grün", () => {
    renderCard({ status: "unknown", last_seen_at: null, credential: { id: "t", kind: "api_token", username: "root@pam!deck", port: 8006 } });
    expect(screen.getByText("Zugriffstoken · root@pam!deck").className).toContain("emerald");
    expect(screen.queryByTestId("access-hint")).toBeNull();
  });

  it("ohne Zugang: „Kein Zugang“", () => {
    renderCard({ credential: null });
    expect(screen.getByText("Kein Zugang")).toBeInTheDocument();
    expect(screen.queryByTestId("access-hint")).toBeNull();
  });

  it("eine gelungene Prüfung macht das Abzeichen grün, eine misslungene Anmeldung nennt es beim Namen", async () => {
    renderCard({ status: "unknown", last_seen_at: null }, { "POST /hosts/h1/check": checkFixture() });
    fireEvent.click(screen.getByRole("button", { name: "Verbindung prüfen" }));
    await screen.findByText("Anmeldung als lattice");
    expect(screen.getByText("Schlüssel · nodvard").className).toContain("emerald");
    expect(screen.queryByTestId("access-hint")).toBeNull();
  });

  it("Anmeldung gescheitert: „Anmeldung klappt nicht“, nicht grün", async () => {
    const failed = checkFixture({
      ok: false,
      items: [
        { id: "reachable", label: "Server erreichbar", status: "ok", detail: "SSH-Dienst antwortet.", hint: "" },
        { id: "login", label: "Anmeldung als nodvard", status: "fail", detail: "Abgelehnt.", hint: "" },
      ],
    });
    renderCard({ status: "up", last_seen_at: "2026-10-01T10:00:00Z" }, { "POST /hosts/h1/check": failed });
    fireEvent.click(screen.getByRole("button", { name: "Verbindung prüfen" }));
    await screen.findByText("Anmeldung als nodvard");
    expect(screen.getByText("Schlüssel · nodvard").className).not.toContain("emerald");
    expect(screen.getByText("Anmeldung klappt nicht")).toBeInTheDocument();
  });
});
