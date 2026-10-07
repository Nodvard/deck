import { describe, expect, it } from "vitest";

import { actionLabel, actionTitle, rawActionTitle } from "./actionNames";

const BASE = { ext_id: "shield", action_type: "nexus_soc.upgrade" };

describe("actionLabel", () => {
  it("nimmt den Namen der Aktionsart, sonst die Kennung der Art", () => {
    expect(actionLabel({ ...BASE, action_label: "Updates einspielen" })).toBe("Updates einspielen");
    expect(actionLabel(BASE)).toBe("nexus_soc.upgrade");
    expect(actionLabel({ ...BASE, action_label: null })).toBe("nexus_soc.upgrade");
    expect(actionLabel({ ...BASE, action_label: "  " })).toBe("nexus_soc.upgrade");
  });
});

describe("actionTitle", () => {
  it("zeigt Namen von Erweiterung und Aktionsart", () => {
    expect(actionTitle({ ...BASE, ext_name: "Nodvard Shield", action_label: "Updates einspielen" })).toBe("Nodvard Shield · Updates einspielen");
  });

  it("ersetzt einen fehlenden Teil durch seine rohe Kennung", () => {
    expect(actionTitle({ ...BASE, ext_name: "Nodvard Shield" })).toBe("Nodvard Shield · nexus_soc.upgrade");
    expect(actionTitle({ ...BASE, action_label: "Updates einspielen" })).toBe("shield · Updates einspielen");
  });

  it("ohne beide Namen (Erweiterung entfernt, älteres Backend) bleibt es wie vorher bei den Kennungen", () => {
    expect(actionTitle(BASE)).toBe("shield/nexus_soc.upgrade");
    expect(actionTitle({ ...BASE, ext_name: null, action_label: null })).toBe(rawActionTitle(BASE));
  });

  it("Befehle von der Server-Seite (`core`, keine Erweiterung) heißen nur nach der Aktion", () => {
    const fromHostPage = { ext_id: "core", action_type: "shell.exec" };
    expect(actionTitle({ ...fromHostPage, action_label: "Shell-Befehl ausführen", ext_name: null })).toBe("Shell-Befehl ausführen");
    expect(actionTitle({ ...fromHostPage, action_label: "Shell-Befehl ausführen" })).toBe("Shell-Befehl ausführen");
    // Ohne Namen der Aktion bleibt es wie vorher bei den Kennungen.
    expect(actionTitle(fromHostPage)).toBe("core/shell.exec");
    expect(actionTitle({ ...fromHostPage, action_label: null, ext_name: null })).toBe("core/shell.exec");
  });
});
