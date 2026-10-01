import type { ActionsView } from "../types";

export const view: ActionsView = {
  kind: "actions",
  actions: [
    {
      id: "restart",
      label: "Neustart",
      endpoint: "actions/restart",
      method: "POST",
      body: null,
      confirm: true,
      confirm_text: "Wirklich neu starten?",
      style: "danger",
      permissions: [],
    },
  ],
};

export const data = {};

export const expectContains = ["Neustart"];
