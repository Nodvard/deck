import { describe, expect, it } from "vitest";

import { helperHintTarget, helperHintText, hostHealth, hostStatusText } from "./overview";

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

describe("helperHintText", () => {
  const hint = { host_id: "h", host_name: "Heimserver", checked_at: "2026-10-24T00:00:00Z", without_root: true };

  it("sagt bei einem Modul, das sein altes Skript weiter aufruft, nicht „kein root“", () => {
    expect(helperHintText({ ...hint, modules: ["Docker"], still_used: ["Docker"] })).toBe(
      "Heimserver: Server neu einrichten, denn Docker nutzt dort noch ein älteres Hilfsskript",
    );
  });

  it("trennt Module ohne root von denen mit alter Fassung", () => {
    expect(helperHintText({ ...hint, modules: ["Nodvard Shield", "Docker"], still_used: ["Docker"] })).toBe(
      "Heimserver: Server neu einrichten, sonst hat Nodvard Shield dort kein root, und Docker nutzt dort noch ein älteres Hilfsskript",
    );
    expect(helperHintText({ ...hint, modules: ["Nodvard Shield", "Server-Zustand"] })).toBe(
      "Heimserver: Server neu einrichten, sonst haben Nodvard Shield und Server-Zustand dort kein root",
    );
  });

  it("bleibt mit voller sudo-Regel bei „mit engen Rechten“", () => {
    expect(helperHintText({ ...hint, without_root: false, modules: ["Docker"], still_used: ["Docker"] })).toBe(
      "Heimserver: Server neu einrichten, damit Docker mit engen Rechten läuft",
    );
  });

  it("sagt mit voller sudo-Regel „kein root“ für ein Modul, das nur sein Skript nutzt", () => {
    const full = { ...hint, without_root: false, no_fallback: ["Docker"] };
    expect(helperHintText({ ...full, modules: ["Docker"] })).toBe("Heimserver: Server neu einrichten, sonst hat Docker dort kein root");
    expect(helperHintText({ ...full, modules: ["Nodvard Shield", "Docker"] })).toBe(
      "Heimserver: Server neu einrichten, damit Nodvard Shield mit engen Rechten läuft, sonst hat Docker dort kein root",
    );
    expect(helperHintText({ ...full, modules: ["Docker"], still_used: ["Docker"] })).toBe(
      "Heimserver: Server neu einrichten, damit Docker mit engen Rechten läuft",
    );
  });

  it("sagt, wenn die volle sudo-Regel nicht nötig ist", () => {
    const removable = { ...hint, without_root: false, full_rule_removable: true };
    expect(helperHintText({ ...removable, modules: [] })).toBe(
      "Heimserver: die volle sudo-Regel ist dort nicht nötig, die Hilfsskripte reichen",
    );
    expect(helperHintText({ ...removable, modules: ["Docker"], still_used: ["Docker"] })).toBe(
      "Heimserver: Server neu einrichten, damit Docker mit engen Rechten läuft; die volle sudo-Regel ist dort nicht nötig, die Hilfsskripte reichen",
    );
  });

  it("führt zum Einrichtungsbefehl, nur mit unnötiger voller Regel gleich mit „Volle sudo-Regel entfernen“", () => {
    const base = { ...hint, host_id: "h-pi", without_root: false };
    expect(helperHintTarget({ ...base, modules: ["Docker"], full_rule_removable: true })).toBe("/settings/hosts/h-pi?befehl=1");
    expect(helperHintTarget({ ...base, modules: [], full_rule_removable: true })).toBe("/settings/hosts/h-pi?befehl=regel-entfernen");
    expect(helperHintTarget({ ...base, modules: [] })).toBe("/settings/hosts/h-pi");
  });
});
