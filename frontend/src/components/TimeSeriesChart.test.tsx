import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { stats, TimeSeriesChart } from "./TimeSeriesChart";

function renderChart(values: (number | null)[]) {
  const timestamps = values.map((_, i) => 1_700_000_000 + i * 30);
  const series = [{ key: "cpu", label: "CPU", color: "#3987e5", values }];
  return render(<TimeSeriesChart title="CPU" timestamps={timestamps} series={series} format={(v) => String(Math.round(v))} rangeSeconds={3600} />);
}

describe("TimeSeriesChart", () => {
  it("zeichnet normale Werte", () => {
    const { container } = renderChart([10, 20, null, 40]);
    expect(container.innerHTML).not.toContain("NaN");
    expect(container.querySelectorAll("path[stroke]")).toHaveLength(1);
  });

  it("zeigt kein NaN bei ungültigen Werten", () => {
    const { container } = renderChart([10, NaN, Infinity, -Infinity, 40]);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    expect(container.querySelector("path[stroke]")!.getAttribute("d")!.match(/M/g)).toHaveLength(2);
  });

  it("meldet ohne gültigen Wert, dass es keine Messwerte gibt", () => {
    const { container } = renderChart([NaN, Infinity, null]);
    expect(screen.getByText(/Noch keine Messwerte/)).toBeInTheDocument();
    expect(container.innerHTML).not.toContain("NaN");
  });

  it("zeichnet eine sehr lange Reihe ohne Fehler", () => {
    const values = Array.from({ length: 150_000 }, (_, i) => (i === 7 ? NaN : i % 100));
    const { container } = renderChart(values);
    expect(container.innerHTML).not.toContain("NaN");
    expect(screen.getAllByText("99", { selector: "td" })).toHaveLength(2);
  });
});

describe("stats", () => {
  it("ignoriert Lücken und ungültige Werte", () => {
    expect(stats([null, 2, NaN, 4, Infinity, null])).toEqual({ current: 4, min: 2, avg: 3, max: 4 });
    expect(stats([NaN, null])).toEqual({ current: null, min: null, avg: null, max: null });
  });

  it("wirft bei 200 000 Werten nicht", () => {
    const values = Array.from({ length: 200_000 }, (_, i) => i);
    expect(stats(values)).toMatchObject({ current: 199_999, min: 0, max: 199_999 });
  });
});
