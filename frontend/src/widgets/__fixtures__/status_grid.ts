import type { StatusGridView } from "../types";

export const view: StatusGridView = {
  kind: "status_grid",
  tile_title: "{{ name }}",
  tile_subtitle: "{{ status }}",
  tile_icon: null,
  tile_tone: "{{ status | tone }}",
  tile_link: null,
};

export const data = [
  { name: "worker-1", status: "running" },
  { name: "worker-2", status: "down" },
];

export const expectContains = ["worker-1", "running", "worker-2", "down"];
