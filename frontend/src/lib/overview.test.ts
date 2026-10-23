import { describe, expect, it } from "vitest";

import { hostHealth, hostStatusText } from "./overview";

const base = { status: "unknown", last_seen_at: null, power_state: null, gone_since: null, provider_ext_id: null };

describe("hostStatusText", () => {
  it("nennt einen von Hand angelegten Server ohne Prüfung „noch nicht geprüft“", () => {
    expect(hostStatusText(base)).toBe("noch nicht geprüft");
  });

  it("nennt einen eingelesenen Server, dessen Zustand das Modul nicht lesen kann, „unbekannt“", () => {
    expect(hostStatusText({ ...base, provider_ext_id: "proxmox", power_state: "running" })).toBe("unbekannt");
  });

  it("nennt einen Knoten einer nicht erreichbaren Verbindung „nicht erreichbar“", () => {
    expect(hostStatusText({ ...base, status: "down", provider_ext_id: "proxmox" })).toBe("nicht erreichbar");
    expect(hostHealth("down")).toBe("problem");
  });
});
