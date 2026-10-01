import type { GaugeView } from "../types";

export const view: GaugeView = {
  kind: "gauge",
  value_field: "used",
  max_field: null,
  max_value: 100,
  label: "Speicher (%)",
};

export const data = { used: 73 };

export const expectContains = ["73", "100", "Speicher (%)"];
