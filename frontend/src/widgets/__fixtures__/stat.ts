import type { StatView } from "../types";

export const view: StatView = {
  kind: "stat",
  value: "{{ count | number }}",
  label: "Aktive Hosts",
  delta: "{{ delta }}",
  tone: "good",
  sparkline_field: null,
};

export const data = { count: 1234, delta: "+3 seit gestern" };

export const expectContains = ["1.234", "Aktive Hosts", "+3 seit gestern"];
