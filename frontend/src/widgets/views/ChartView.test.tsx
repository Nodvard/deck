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
});
