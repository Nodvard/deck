import type { ChartView } from "../types";

export const view: ChartView = {
  kind: "chart",
  chart: "line",
  x_field: "ts",
  series: [{ field: "cpu", label: "CPU", tone: "accent" }],
  y_unit: "percent",
  y_max: 100,
};

export const data = [
  { ts: "2026-09-16T10:00:00Z", cpu: 10 },
  { ts: "2026-09-16T10:01:00Z", cpu: 40 },
];

export const expectContains = ["CPU"];
