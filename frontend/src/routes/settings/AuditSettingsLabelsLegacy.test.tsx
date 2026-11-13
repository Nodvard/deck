import { describe, expect, it } from "vitest";

import { auditActionLabel } from "./AuditSettings";

/** Ältere Protokolleinträge bekommen dieselbe Beschriftung wie neue Einträge von Nodvard Shield. */
describe("auditActionLabel: frühere Vorsilbe von Nodvard Shield", () => {
  it("gibt älteren und neuen Einträgen dieselbe Beschriftung", () => {
    expect(auditActionLabel("nexus_soc.quarantine")).toBe("Datei in Quarantäne verschoben");
    expect(auditActionLabel("nexus_soc.guard_new_port")).toBe(auditActionLabel("shield.guard_new_port"));
    expect(auditActionLabel("nexus_soc.incident")).toBe("Vorfall erfasst und von Nodvard KI bewertet");
  });

  it("liefert für einen Namen, den es auch unter `shield.` nicht gibt, nichts", () => {
    expect(auditActionLabel("nexus_soc.gibt_es_nicht")).toBeUndefined();
  });
});
