import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChartView as ChartViewSpec } from "../types";
import { ChartView } from "./ChartView";

const VIEW: ChartViewSpec = {
  kind: "chart",
  chart: "line",
  x_field: "t",
  series: [{ field: "cpu", label: "CPU", tone: "accent" }],
  y_unit: "number",
  y_max: null,
};

const DATA = [{ cpu: 10 }, { cpu: 50 }, { cpu: 20 }];

/**
 * Vorher liess sich aus dem
 * SVG kein exakter Wert ablesen (kein Achsen-Label, kein Tooltip) -- dieser Test
 * beweist beides.
 */
describe("ChartView", () => {
  it("zeigt min/max als Y-Achsen-Beschriftung", () => {
    render(<ChartView view={VIEW} data={DATA} />);
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("zeigt beim Hover ueber eine Spalte den exakten Wert als Tooltip", () => {
    const { container } = render(<ChartView view={VIEW} data={DATA} />);
    expect(container.textContent).not.toContain("CPU: 50");

    const hoverZones = container.querySelectorAll("rect");
    // Die zweite Hover-Zone entspricht Index 1 (Wert 50).
    fireEvent.mouseEnter(hoverZones[1]);

    expect(container.textContent).toContain("CPU: 50");
  });

  it("blendet den Tooltip beim Verlassen wieder aus", () => {
    const { container } = render(<ChartView view={VIEW} data={DATA} />);
    const hoverZones = container.querySelectorAll("rect");
    fireEvent.mouseEnter(hoverZones[1]);
    expect(container.textContent).toContain("CPU: 50");

    fireEvent.mouseLeave(hoverZones[1]);
    expect(container.textContent).not.toContain("CPU: 50");
  });

  describe("ungültige Werte", () => {
    const BAD = [{ cpu: 10 }, { cpu: "abc" }, { cpu: Infinity }, { cpu: 40 }];

    it.each(["line", "area", "bar"] as const)("%s: kein NaN im SVG", (chart) => {
      const { container } = render(<ChartView view={{ ...VIEW, chart }} data={BAD} />);
      expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    });

    it("lässt in der Linie eine Lücke statt eines Punkts", () => {
      const { container } = render(<ChartView view={VIEW} data={BAD} />);
      const d = container.querySelector("path")!.getAttribute("d")!;
      expect(d.match(/M/g)).toHaveLength(2);
      expect(d).not.toContain("L");
    });

    it("zeichnet bei Balken nur die gültigen", () => {
      const { container } = render(<ChartView view={{ ...VIEW, chart: "bar" }} data={BAD} />);
      expect(container.querySelectorAll("g > rect")).toHaveLength(2);
    });

    it("zeigt im Tooltip einen Strich", () => {
      const { container } = render(<ChartView view={VIEW} data={BAD} />);
      fireEvent.mouseEnter(container.querySelectorAll("svg > rect")[1]);
      expect(container.textContent).toContain("CPU: –");
    });

    it("nimmt für die Achse nur gültige Werte", () => {
      render(<ChartView view={VIEW} data={BAD} />);
      expect(screen.getByText("40")).toBeInTheDocument();
    });

    it("zeigt ohne gültigen Wert die Standardachse", () => {
      const { container } = render(<ChartView view={VIEW} data={[{ cpu: NaN }, { cpu: "x" }]} />);
      expect(container.innerHTML).not.toContain("NaN");
      expect(screen.getByText("1")).toBeInTheDocument();
    });
  });

  it("zeichnet eine lange Reihe ohne Fehler", () => {
    const data = Array.from({ length: 5000 }, (_, i) => ({ cpu: i % 100 }));
    const { container } = render(<ChartView view={VIEW} data={data} />);
    expect(container.innerHTML).not.toContain("NaN");
    expect(screen.getByText("99")).toBeInTheDocument();
  });
});
