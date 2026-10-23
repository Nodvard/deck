import { describe, expect, it } from "vitest";

import {
  attentionOf, fullGroups, groupOf, groupsRenamed, mergeDraftGroups, orderedGroups, renameGroup, toggleGroup, worstAttention,
} from "./groups";
import type { WidgetOut } from "./types";

const base = {
  id: "w", ext_id: "e", title: "W", icon: null, description: null, size: { w: 2, h: 2, min_w: 1, min_h: 1 },
  refresh: { interval_s: null, ws_channel: null }, data_endpoint: "x", permissions: [], component: null, default_enabled: true,
};
const widget = (view: WidgetOut["view"], group?: WidgetOut["group"]): WidgetOut => ({ ...base, view, group });

describe("Gruppen der Startseite", () => {
  it("Reihenfolge: gespeicherte zuerst, fehlende in der Vorgabe dahinter, unbekannte fallen weg", () => {
    const groups = orderedGroups([
      { key: "backups", title: "  ", collapsed: true },
      { key: "nope" as never, title: "x", collapsed: false },
    ]);
    expect(groups.map((g) => g.key)).toEqual(["backups", "servers", "security", "apps", "services"]);
    expect(groups[0]).toEqual({ key: "backups", title: "Backups", collapsed: true });
  });

  it("umschalten speichert alle Gruppen und behält eigene Namen", () => {
    const next = toggleGroup([{ key: "apps", title: "Medien", collapsed: false }], "apps");
    expect(next[0]).toEqual({ key: "apps", title: "Medien", collapsed: true });
    expect(next).toHaveLength(5);
    expect(toggleGroup(next, "apps")[0].collapsed).toBe(false);
  });

  it("die volle Liste behält eigene Namen und setzt für alle anderen null", () => {
    const full = fullGroups([{ key: "apps", title: "Medien", collapsed: true }]);
    expect(full.map((g) => g.key)).toEqual(["apps", "servers", "security", "backups", "services"]);
    expect(full[0]).toEqual({ key: "apps", title: "Medien", collapsed: true });
    expect(full[1]).toEqual({ key: "servers", title: null, collapsed: false });
    expect(fullGroups(undefined)).toHaveLength(5);
  });

  it("umbenennen ändert nur eine Gruppe; beim Speichern wird getrimmt, leer oder der Name der Vorgabe heißt Vorgabe", () => {
    const saved = [{ key: "apps" as const, title: "Medien", collapsed: false }];
    const start = orderedGroups(saved);
    const renamed = renameGroup(start, "backups", "  Sicherungen ");
    expect(renamed.find((g) => g.key === "backups")!.title).toBe("  Sicherungen ");
    expect(renamed.filter((g, i) => g !== start[i])).toHaveLength(1);
    const merged = mergeDraftGroups(saved, start, renameGroup(renamed, "apps", "   "));
    expect(merged.find((g) => g.key === "backups")!.title).toBe("Sicherungen");
    expect(merged.find((g) => g.key === "apps")!.title).toBeNull();
    expect(merged.find((g) => g.key === "servers")!.title).toBeNull();
    const asDefault = mergeDraftGroups(saved, start, renameGroup(start, "apps", " Docker und Apps "));
    expect(asDefault.find((g) => g.key === "apps")!.title).toBeNull();
  });

  it("„Fertig“: Reihenfolge und Zuklappen aus dem aktuellen Stand, Namen nur, wenn sie im Modus geändert wurden", () => {
    const start = orderedGroups([{ key: "servers", title: "Maschinen", collapsed: false }]);
    const draft = renameGroup(renameGroup(start, "apps", "Medien"), "servers", " Maschinen ");
    const current = [
      { key: "backups" as const, title: null, collapsed: true },
      { key: "servers" as const, title: "Rechner", collapsed: false },
      { key: "apps" as const, title: "Container", collapsed: true },
      { key: "security" as const, title: "Schutz", collapsed: false },
    ];
    expect(mergeDraftGroups(current, start, draft)).toEqual([
      { key: "backups", title: null, collapsed: true },
      { key: "servers", title: "Rechner", collapsed: false },
      { key: "apps", title: "Medien", collapsed: true },
      { key: "security", title: "Schutz", collapsed: false },
      { key: "services", title: null, collapsed: false },
    ]);
  });

  it("als umbenannt zählt nur, was sich gespeichert anders liest", () => {
    const start = orderedGroups([{ key: "servers", title: "Maschinen", collapsed: false }]);
    expect(groupsRenamed(start, renameGroup(start, "servers", " Maschinen "))).toBe(false);
    expect(groupsRenamed(start, renameGroup(start, "apps", " Docker und Apps"))).toBe(false);
    expect(groupsRenamed(start, renameGroup(start, "apps", "Medien"))).toBe(true);
    expect(groupsRenamed(start, renameGroup(start, "servers", ""))).toBe(true);
  });

  it("„Fertig“ ohne gespeicherte Gruppen: alle in der Vorgabe-Reihenfolge, eigene Namen aus dem Modus", () => {
    const start = orderedGroups(undefined);
    const merged = mergeDraftGroups(undefined, start, renameGroup(start, "services", "Sonstiges"));
    expect(merged.map((g) => g.key)).toEqual(["servers", "security", "apps", "backups", "services"]);
    expect(merged.filter((g) => g.title !== null)).toEqual([{ key: "services", title: "Sonstiges", collapsed: false }]);
  });

  it("Zuordnung: eigene Wahl vor Widget vor „Dienste“, unbekannte Schlüssel zählen nicht", () => {
    const w = widget({ kind: "markdown", content_field: "x" }, "servers");
    expect(groupOf({ group: "apps" }, w)).toBe("apps");
    expect(groupOf({ group: "irgendwas" }, w)).toBe("servers");
    expect(groupOf({}, widget({ kind: "markdown", content_field: "x" }))).toBe("services");
  });

  it("Warnsignal aus Zahl, Liste und Kachelraster; das Schlimmste gewinnt", () => {
    const stat = widget({ kind: "stat", value: "1", label: null, delta: null, tone: "{{ tone }}", sparkline_field: null });
    expect(attentionOf(stat, { data: { tone: "warn" }, meta: {} })).toBe("warn");
    expect(attentionOf(stat, { data: { tone: "good" }, meta: {} })).toBeNull();
    const list = widget({
      kind: "list", empty_text: "", max_items: null,
      item: { title: "{{ t }}", subtitle: null, icon: null, badge: { text: "x", tone: "{{ tone }}" }, actions: [] },
    });
    expect(attentionOf(list, { data: [{ tone: "warn" }, { tone: "danger" }, { tone: "good" }], meta: {} })).toBe("danger");
    const grid = widget({ kind: "status_grid", tile_title: "t", tile_subtitle: null, tile_icon: null, tile_tone: "{{ tone }}", tile_link: null });
    expect(attentionOf(grid, { data: [{ tone: "neutral" }, { tone: "warn" }], meta: {} })).toBe("warn");
    expect(attentionOf(grid, undefined)).toBeNull();
  });

  it("das Schlimmste gewinnt: Störung vor Warnung vor „lädt nicht“", () => {
    expect(worstAttention([null, "unknown", "warn"])).toBe("warn");
    expect(worstAttention(["warn", "danger", "unknown"])).toBe("danger");
    expect(worstAttention([null, "unknown"])).toBe("unknown");
    expect(worstAttention([null])).toBeNull();
  });
});
