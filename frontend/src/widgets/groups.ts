/**
 * Gruppen der Startseite: Schlüssel wie `WidgetSpec.group` im SDK, Namen und Reihenfolge der Vorgabe, Zuordnung der
 * Widgets und das Signal für den Warnpunkt einer zugeklappten Gruppe.
 */
import { renderTone } from "./template";
import type { Tone, WidgetGroupKey, WidgetOut } from "./types";

export type { WidgetGroupKey };

export interface DashboardGroupState {
  key: WidgetGroupKey;
  /** Eigener Name; leer heißt der Name der Vorgabe. */
  title?: string | null;
  collapsed: boolean;
}

export const WIDGET_GROUPS: { key: WidgetGroupKey; title: string }[] = [
  { key: "servers", title: "Server und Proxmox" },
  { key: "security", title: "Sicherheit" },
  { key: "apps", title: "Docker und Apps" },
  { key: "backups", title: "Backups" },
  { key: "services", title: "Dienste" },
];

const KEYS = new Set<string>(WIDGET_GROUPS.map((g) => g.key));

export function isGroupKey(value: unknown): value is WidgetGroupKey {
  return typeof value === "string" && KEYS.has(value);
}

/** Die Gruppe eines Eintrags: von der Person gewählt (`config.group`), sonst vom Widget, sonst „Dienste“. */
export function groupOf(config: Record<string, unknown> | undefined, widget: WidgetOut | undefined): WidgetGroupKey {
  if (isGroupKey(config?.group)) return config.group;
  if (isGroupKey(widget?.group)) return widget.group;
  return "services";
}

/** Alle Gruppen in der gespeicherten Reihenfolge, fehlende in der Vorgabe-Reihenfolge dahinter. */
export function orderedGroups(saved: DashboardGroupState[] | undefined): (DashboardGroupState & { title: string })[] {
  const byKey = new Map((saved ?? []).filter((g) => isGroupKey(g.key)).map((g) => [g.key, g]));
  const order = [...byKey.keys(), ...WIDGET_GROUPS.map((g) => g.key).filter((k) => !byKey.has(k))];
  return order.map((key) => {
    const own = byKey.get(key);
    return { key, title: own?.title?.trim() || defaultGroupTitle(key), collapsed: own?.collapsed ?? false };
  });
}

export function defaultGroupTitle(key: WidgetGroupKey): string {
  return WIDGET_GROUPS.find((g) => g.key === key)!.title;
}

/** Alle Gruppen in voller Liste, wie sie gespeichert werden: eigener Name (`null` heißt Vorgabe) und Zustand. */
export function fullGroups(saved: DashboardGroupState[] | undefined): DashboardGroupState[] {
  const own = new Map((saved ?? []).map((g) => [g.key, g]));
  return orderedGroups(saved).map((g) => ({ key: g.key, title: own.get(g.key)?.title ?? null, collapsed: g.collapsed }));
}

/** Die gespeicherte Fassung nach dem Umschalten einer Gruppe; alle Gruppen, damit die Reihenfolge feststeht. */
export function toggleGroup(saved: DashboardGroupState[] | undefined, key: WidgetGroupKey): DashboardGroupState[] {
  return fullGroups(saved).map((g) => (g.key === key ? { ...g, collapsed: !g.collapsed } : g));
}

export function renameGroup(groups: DashboardGroupState[], key: WidgetGroupKey, title: string): DashboardGroupState[] {
  return groups.map((g) => (g.key === key ? { ...g, title } : g));
}

/** Ein Name, wie er gespeichert wird: ohne Leerraum am Rand; ein leerer oder der der Vorgabe wird `null`. */
function cleanTitle(key: WidgetGroupKey, title: string | null | undefined): string | null {
  const trimmed = title?.trim();
  return trimmed && trimmed !== defaultGroupTitle(key) ? trimmed : null;
}

function isRenamed(edited: DashboardGroupState, start: DashboardGroupState[]): boolean {
  return cleanTitle(edited.key, edited.title) !== cleanTitle(edited.key, start.find((s) => s.key === edited.key)?.title);
}

/** Ob im Bearbeiten-Modus ein Gruppenname geändert wurde; sonst speichert „Fertig“ die Gruppen gar nicht. */
export function groupsRenamed(start: DashboardGroupState[], draft: DashboardGroupState[]): boolean {
  return draft.some((d) => isRenamed(d, start));
}

/**
 * Was „Fertig“ für die Gruppen speichert: Reihenfolge und Zuklappen aus dem aktuellen Stand, den Namen aus dem Entwurf
 * nur, wenn er sich seit `start` geändert hat. Was ein anderes Gerät inzwischen gespeichert hat, bleibt so stehen.
 */
export function mergeDraftGroups(
  current: DashboardGroupState[] | undefined, start: DashboardGroupState[], draft: DashboardGroupState[],
): DashboardGroupState[] {
  return fullGroups(current).map((g) => {
    const edited = draft.find((d) => d.key === g.key);
    return { ...g, title: cleanTitle(g.key, edited && isRenamed(edited, start) ? edited.title : g.title) };
  });
}

const RANK: Partial<Record<Tone, number>> = { warn: 1, danger: 2 };

/** Was der Punkt einer zugeklappten Gruppe meldet; „unknown“ heißt, ein Widget lädt gerade nicht. */
export type Attention = "danger" | "warn" | "unknown";

const ATTENTION_RANK: Record<Attention, number> = { unknown: 1, warn: 2, danger: 3 };

export function worstAttention(found: (Attention | null)[]): Attention | null {
  let worst: Attention | null = null;
  for (const a of found) if (a && (!worst || ATTENTION_RANK[a] > ATTENTION_RANK[worst])) worst = a;
  return worst;
}

/** Das Schlimmste, was ein Widget gerade zeigt („warn“ oder „danger“), sonst `null`. Listen zählen alle Zeilen, auch die
 * hinter „+ x weitere“: Zugeklappt soll nichts untergehen. */
export function attentionOf(widget: WidgetOut, response: unknown): "warn" | "danger" | null {
  if (typeof response !== "object" || response === null) return null;
  const data = (response as { data?: unknown }).data;
  const rows = Array.isArray(data) ? data : [];
  const tones: Tone[] = [];
  const view = widget.view;
  if (view.kind === "stat") tones.push(renderTone(view.tone, data));
  if (view.kind === "status_grid") for (const row of rows) tones.push(renderTone(view.tile_tone, row));
  if (view.kind === "list" && view.item.badge) for (const row of rows) tones.push(renderTone(view.item.badge.tone, row));
  let worst = 0;
  for (const tone of tones) worst = Math.max(worst, RANK[tone] ?? 0);
  return worst === 2 ? "danger" : worst === 1 ? "warn" : null;
}
