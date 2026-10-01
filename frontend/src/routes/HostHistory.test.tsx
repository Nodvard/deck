import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function renderHistory(calls: string[], status = 200) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    const range = new URL(url, "http://x").searchParams.get("range") ?? "1h";
    if (status !== 200) return new Response(JSON.stringify({ detail: "Kein MetricsProvider" }), { status });
    return new Response(JSON.stringify(history(range)), { status: 200 });
  }));
  useAuthStore.setState({ accessToken: "tok", status: "authenticated" } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HostHistory hostId="h-pi" />
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

  it("Statistik ignoriert Luecken", () => {
    expect(stats([null, 2, null, 4])).toEqual({ current: 4, min: 2, avg: 3, max: 4 });
    expect(stats([null, null])).toEqual({ current: null, min: null, avg: null, max: null });
  });
});
