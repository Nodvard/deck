import type { TableView } from "../types";

export const view: TableView = {
  kind: "table",
  columns: [
    { field: "name", label: "Name", template: null, align: "left", width: null },
    { field: "usage", label: "Auslastung", template: "{{ usage | percent }}", align: "right", width: null },
  ],
  row_actions: [],
  empty_text: "Keine Daten",
};

export const data = [{ name: "host-1", usage: 42.5 }];

export const expectContains = ["host-1", "42,5%"];
