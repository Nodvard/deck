import type { LogView } from "../types";

export const view: LogView = {
  kind: "log",
  lines_field: "lines",
  follow: true,
  max_lines: 500,
};

export const data = { lines: ["Zeile 1", "Zeile 2"] };

export const expectContains = ["Zeile 1", "Zeile 2"];
