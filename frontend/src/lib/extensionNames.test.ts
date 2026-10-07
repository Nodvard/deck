import { describe, expect, it } from "vitest";

import { extensionName, findExtension } from "./extensionNames";
import type { ExtensionInfo } from "./firstSteps";

const SHIELD: ExtensionInfo = { id: "shield", name: "Nodvard Shield", legacy_ids: ["nexus-soc"], state: "enabled" };
const PROXMOX: ExtensionInfo = { id: "proxmox", name: "Proxmox VE", state: "enabled" };

describe("extensionName", () => {
  it("nennt zur heutigen Kennung den Namen", () => {
    expect(extensionName([SHIELD, PROXMOX], "shield")).toBe("Nodvard Shield");
    expect(extensionName([SHIELD, PROXMOX], "proxmox")).toBe("Proxmox VE");
  });

  it("nennt zu einer früheren Kennung den Namen der umbenannten Erweiterung", () => {
    expect(extensionName([SHIELD, PROXMOX], "nexus-soc")).toBe("Nodvard Shield");
    expect(findExtension([SHIELD, PROXMOX], "nexus-soc")?.id).toBe("shield");
  });

  it("die heutige Kennung geht vor: eine frühere kann inzwischen einer anderen Erweiterung gehören", () => {
    const newcomer: ExtensionInfo = { id: "nexus-soc", name: "Etwas anderes", state: "enabled" };
    expect(extensionName([SHIELD, newcomer], "nexus-soc")).toBe("Etwas anderes");
    expect(extensionName([newcomer, SHIELD], "nexus-soc")).toBe("Etwas anderes");
  });

  it("ohne Treffer, ohne Namen oder ohne geladene Liste steht die Kennung da", () => {
    expect(extensionName([SHIELD, PROXMOX], "gibt-es-nicht")).toBe("gibt-es-nicht");
    expect(extensionName([{ id: "stumm", name: null, state: "enabled" }], "stumm")).toBe("stumm");
    expect(extensionName(undefined, "shield")).toBe("shield");
  });

  it("ohne `legacy_ids` (ältere Backends, keine Umbenennung) bleibt es bei der heutigen Kennung", () => {
    expect(extensionName([PROXMOX], "nexus-soc")).toBe("nexus-soc");
  });
});
