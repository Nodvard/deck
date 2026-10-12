import { describe, expect, it } from "vitest";

import { auditActionLabel } from "./AuditSettings";

/**
 * Bis 0.6 hieß Nodvard Shield `nexus_soc`; Protokolleinträge von damals tragen das als Vorsilbe und werden nicht
 * umgeschrieben. Sie bekommen dieselbe Beschriftung wie die neuen Einträge (`shield.<Vorgang>`).
 */
describe("auditActionLabel: frühere Vorsilbe von Nodvard Shield", () => {
  it("gibt Einträgen mit `nexus_soc.` dieselbe Beschriftung wie denen mit `shield.`", () => {
    expect(auditActionLabel("nexus_soc.quarantine")).toBe("Datei in Quarantäne verschoben");
    expect(auditActionLabel("nexus_soc.guard_new_port")).toBe(auditActionLabel("shield.guard_new_port"));
    expect(auditActionLabel("nexus_soc.incident")).toBe("Vorfall erfasst und von Nodvard KI bewertet");
  });

  it("liefert für einen Namen, den es auch unter `shield.` nicht gibt, nichts", () => {
    expect(auditActionLabel("nexus_soc.gibt_es_nicht")).toBeUndefined();
  });
});
