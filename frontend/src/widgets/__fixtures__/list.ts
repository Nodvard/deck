import type { ListView } from "../types";

export const view: ListView = {
  kind: "list",
  item: {
    title: "{{ title }}",
    subtitle: "{{ host }} · {{ ts | relative }}",
    icon: null,
    badge: { text: "{{ severity }}", tone: "{{ severity | tone }}" },
    actions: [
      {
        id: "confirm",
        label: "Bestaetigen",
        endpoint: "incidents/{{ id }}/confirm",
        method: "POST",
        body: null,
        confirm: false,
        confirm_text: null,
        style: "primary",
        permissions: [],
      },
    ],
  },
  empty_text: "Keine offenen Vorfaelle",
  max_items: null,
};

export const data = [
  { id: "1", title: "Hoher CPU-Verbrauch", host: "host-1", severity: "critical", ts: new Date().toISOString() },
];

export const expectContains = ["Hoher CPU-Verbrauch", "host-1", "critical", "Bestaetigen"];
