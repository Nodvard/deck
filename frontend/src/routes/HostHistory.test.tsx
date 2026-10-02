import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { stats } from "../components/TimeSeriesChart";
import { useAuthStore } from "../state/auth";
import { HostHistory } from "./HostHistory";

const GB = 1024 ** 3;

function history(range: string) {
  return {
    range, source: "lattice", step_s: 30, timestamps: [100, 130, 160, 190], max: {},
    series: {
      cpu_percent: [10, 30, null, 50],
      mem_used_bytes: [1 * GB, 1.5 * GB, null, 2 * GB],
      mem_total_bytes: [4 * GB, 4 * GB, null, 4 * GB],
      temp_c: [50, 51, null, 52],
      swap_used_bytes: [null, null, null, null],
    },
  };
}

function renderHistory(calls: string[], status = 200, props: Partial<ComponentProps<typeof HostHistory>> = {}, empty = false, source = "lattice") {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    const range = new URL(url, "http://x").searchParams.get("range") ?? "1h";
    if (status !== 200) return new Response(JSON.stringify({ detail: "Kein MetricsProvider" }), { status });
    const body = empty ? { ...history(range), source, series: {} } : history(range);
    return new Response(JSON.stringify(body), { status: 200 });
  }));
  useAuthStore.setState({ accessToken: "tok", status: "authenticated" } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HostHistory hostId="h-pi" {...props} />
    </QueryClientProvider>,
  );
}

describe("HostHistory", () => {
  it("zeigt nur Diagramme mit Werten, Legende mit aktuell/min/Ø/max", async () => {
    const calls: string[] = [];
    renderHistory(calls);
    const cpu = await screen.findByTestId("chart-CPU");
    // aktuell 50, min 10, Mittel 30 (Luecke zaehlt nicht), max 50
    expect(cpu.querySelector("tbody")?.textContent).toBe("Auslastung50 %10 %30 %50 %");
    expect(screen.getByTestId("chart-Arbeitsspeicher")).toBeInTheDocument();
    expect(screen.getByTestId("chart-Temperatur")).toBeInTheDocument();
    expect(screen.queryByTestId("chart-Auslagerung (Swap)")).toBeNull();
    expect(screen.getByText(/Quelle: Eigene Messung · Auflösung 30 s/)).toBeInTheDocument();
  });

  it("Zeitraum-Wahl fragt den passenden Verlauf ab", async () => {
    const calls: string[] = [];
    renderHistory(calls);
    await screen.findByTestId("chart-CPU");
    fireEvent.click(screen.getByRole("button", { name: "7 Tage" }));
    await waitFor(() => expect(calls.some((c) => c.endsWith("/hosts/h-pi/metrics/history?range=7d"))).toBe(true));
    expect(screen.getByRole("button", { name: "7 Tage" })).toHaveAttribute("aria-pressed", "true");
  });

  it("ohne Anbieter (404) verschwindet der Abschnitt ganz", async () => {
    const calls: string[] = [];
    const { container } = renderHistory(calls, 404);
    await waitFor(() => expect(calls.length).toBe(1));
    await waitFor(() => expect(container.querySelector('[data-testid="host-history"]')).toBeNull());
  });

  describe("ohne Messwerte", () => {
    it("Server online: Kurven kommen gleich, mit Gedankenstrich statt „--“", async () => {
      renderHistory([], 200, { host: { status: "up", last_seen_at: "2026-10-01T10:00:00Z" }, canCheck: true }, true);
      const text = (await screen.findByText(/Noch keine Messwerte\./)).textContent ?? "";
      expect(text).toContain("alle 30 Sekunden – in ein paar Minuten erscheinen die ersten Kurven");
      expect(text).not.toContain("--");
      expect(screen.queryByTestId("history-no-connection")).toBeNull();
    });

    it("Server antwortet, Anmeldung nie belegt: Hinweis auf den Zugang statt „in ein paar Minuten“", async () => {
      const credential = { id: "c1", kind: "ssh_key" as const, username: "nodvard", port: 22 };
      renderHistory([], 200, { host: { status: "up", last_seen_at: "2026-10-01T10:00:00Z", credential, login_ok_at: null }, canCheck: true }, true);
      const note = await screen.findByTestId("history-login-unproven");
      expect(note.textContent).toContain("die Anmeldung ist noch nicht bestätigt");
      expect(note.textContent).not.toContain("in ein paar Minuten");
      expect(screen.getByRole("link", { name: "Zum Zugang" })).toHaveAttribute("href", "#zugang");
    });

    it("Anmeldung belegt, aber noch keine Messwerte: Kurven kommen gleich", async () => {
      const credential = { id: "c1", kind: "ssh_key" as const, username: "nodvard", port: 22 };
      renderHistory([], 200, { host: { status: "up", last_seen_at: "2026-10-01T10:00:00Z", credential, login_ok_at: "2026-10-01T10:00:00Z" }, canCheck: true }, true);
      expect((await screen.findByText(/Noch keine Messwerte\./)).textContent).toContain("in ein paar Minuten erscheinen die ersten Kurven");
      expect(screen.queryByTestId("history-login-unproven")).toBeNull();
    });

    it("Server hat nie geantwortet: klarer Hinweis auf den Zugang statt „in ein paar Minuten“", async () => {
      renderHistory([], 200, { host: { status: "down", last_seen_at: null }, canCheck: true }, true);
      const note = await screen.findByTestId("history-no-connection");
      expect(note.textContent).toContain("Noch keine Verbindung – prüfe zuerst den Zugang.");
      expect(note.textContent).not.toContain("in ein paar Minuten");
      expect(screen.getByRole("link", { name: "Zum Zugang" })).toHaveAttribute("href", "#zugang");
    });

    it("noch nie geprüft (Zustand unbekannt) zählt genauso", async () => {
      renderHistory([], 200, { host: { status: "unknown", last_seen_at: null }, canCheck: true }, true);
      expect((await screen.findByTestId("history-no-connection")).textContent).toContain("Noch keine Verbindung");
    });

    it("ohne Recht zum Prüfen: kein Verweis auf eine Karte, die es für die Person nicht gibt", async () => {
      renderHistory([], 200, { host: { status: "down", last_seen_at: null }, canCheck: false }, true);
      const note = await screen.findByTestId("history-no-connection");
      expect(note.textContent).toContain("Noch keine Verbindung zu diesem Server");
      expect(note.textContent).not.toContain("prüfe zuerst");
      expect(screen.queryByRole("link", { name: "Zum Zugang" })).toBeNull();
    });

    it("Server hat früher geantwortet, jetzt nicht: sagt das, ohne auf den Zugang zu verweisen", async () => {
      renderHistory([], 200, { host: { status: "down", last_seen_at: "2026-10-01T09:00:00Z" }, canCheck: true }, true);
      const note = await screen.findByTestId("history-no-connection");
      expect(note.textContent).toContain("der Server antwortet gerade nicht");
      expect(screen.queryByRole("link", { name: "Zum Zugang" })).toBeNull();
    });

    it("Werte vom Anbieter (z. B. Proxmox): bleibt beim allgemeinen Satz", async () => {
      renderHistory([], 200, { host: { status: "stopped", last_seen_at: null }, canCheck: true }, true, "provider");
      await screen.findByText(/Noch keine Messwerte\./);
      expect(screen.queryByTestId("history-no-connection")).toBeNull();
    });
  });

  it("Statistik ignoriert Luecken", () => {
    expect(stats([null, 2, null, 4])).toEqual({ current: 4, min: 2, avg: 3, max: 4 });
    expect(stats([null, null])).toEqual({ current: null, min: null, avg: null, max: null });
  });
});
