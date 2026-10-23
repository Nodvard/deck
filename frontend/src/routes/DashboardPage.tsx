import { useQueries } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type Ref } from "react";
import { GridLayout, useContainerWidth, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";

import { ButtonLink, EmptyState } from "../components/EmptyState";
import { LoadError } from "../components/LoadError";
import { useWidgets } from "../lib/catalog";
import { isHiddenItem, useDashboardLayout, type DashboardLayoutItem } from "../lib/dashboard";
import type { WidgetOut } from "../widgets/types";
import { useAuthStore } from "../state/auth";
import { widthChoice, withGroup, withSize, withWidth, withoutWidget } from "../widgets/edit";
import { MAX_ROWS, MIN_ROWS, ROW_GAP, ROW_HEIGHT, capRows, fixedRowsOf, rowsToPx, sizeKeyOf } from "../widgets/fit";
import {
  attentionOf, defaultGroupTitle, groupOf, groupsRenamed, mergeDraftGroups, orderedGroups, renameGroup, toggleGroup,
  worstAttention,
  type Attention, type DashboardGroupState, type WidgetGroupKey,
} from "../widgets/groups";
import { isNotConfigured, widgetDataQuery } from "../widgets/query";
import { WidgetCard } from "../widgets/WidgetCard";
import { WidgetPickerDialog } from "./WidgetPickerDialog";
import { WidgetEditBar } from "./WidgetEditBar";
import { Cockpit } from "./Cockpit";
import { errorText, inputClass } from "./settings/ui";

const COLS = 12;
// Bei 12 Spalten und 12 px Abstand ist eine w=2-Karte 2*(W-156)/12+12 breit, unter 960 px würde sie unter ~146 px
// schrumpfen. Darunter gibt es kein Raster, sondern die gestapelte Ansicht (STACK_BELOW).
// Bewusst keine responsive Spaltenzahl: Das Layout liegt pro Nutzer in 12er-Koordinaten, react-grid-layout würde bei
// weniger Spalten umsortieren, und ein Zug dort speicherte das umsortierte Layout über das vom Desktop.
const MIN_GRID_WIDTH = 960;
// Schmaler: dieselbe Reihenfolge, ab 640 px in zwei Spalten, ohne Ziehen und ohne Zurückspeichern.
const STACK_BELOW = MIN_GRID_WIDTH;

/**
 * Mindestgröße je View-Typ, damit schmale Widgets ihren Inhalt nicht quetschen (Listen mit Knöpfen brauchen Platz).
 * Gilt auch für gespeicherte Layouts: react-grid-layout bekommt die angehobene Breite und schiebt Kollisionen nach
 * unten. Die Höhe ist nur die Vorgabe, bis die Karte ihren Inhalt gemessen hat (`rowsFor`, src/widgets/fit.ts).
 */
export const MIN_SIZE: Record<string, { w: number; h: number }> = {
  list: { w: 4, h: 3 },
  table: { w: 6, h: 3 },
  status_grid: { w: 4, h: 3 },
  chart: { w: 4, h: 3 },
  log: { w: 6, h: 3 },
  gauge: { w: 3, h: 2 },
  stat: { w: 2, h: 2 },
  markdown: { w: 4, h: 2 },
  actions: { w: 3, h: 2 },
};

export function sizeFor(kind: string | undefined, w: number, h: number): { w: number; h: number } {
  const min = (kind && MIN_SIZE[kind]) || { w: 2, h: 2 };
  return { w: Math.min(COLS, Math.max(w, min.w)), h: Math.max(h, min.h) };
}

/**
 * Dicht packen (erste freie Stelle, zeilenweise, in der bisherigen Reihenfolge) --
 * gebraucht, wenn alte, zu kleine Karten auf ihre Mindestgröße wachsen: an ihren alten
 * x-Positionen würden sie kollidieren und react-grid-layout schöbe sie treppenartig
 * nach unten (live in der Vorschau gesehen: große Lücken).
 */
export function packLayout(entries: { i: string; w: number; h: number }[]): Map<string, { x: number; y: number }> {
  const occupied: boolean[][] = [];
  const free = (x: number, y: number, w: number, h: number) => {
    for (let r = y; r < y + h; r++) for (let c = x; c < x + w; c++) if (occupied[r]?.[c]) return false;
    return true;
  };
  const result = new Map<string, { x: number; y: number }>();
  for (const e of entries) {
    const w = Math.min(COLS, e.w);
    for (let y = 0; !result.has(e.i); y++) {
      for (let x = 0; x + w <= COLS; x++) {
        if (!free(x, y, w, e.h)) continue;
        for (let r = y; r < y + e.h; r++) {
          occupied[r] = occupied[r] ?? [];
          for (let c = x; c < x + w; c++) occupied[r][c] = true;
        }
        result.set(e.i, { x, y });
        break;
      }
    }
  }
  return result;
}

function itemKey(item: { ext_id: string; widget_id: string }): string {
  return `${item.ext_id}:${item.widget_id}`;
}

/** Rasterzeilen einer Karte: die gewählte Größe, sonst die gemessene Stufe, bis zur Messung die Mindesthöhe des Typs. */
export function rowsFor(kind: string | undefined, measured: number | undefined, fallback: number, fixed: number | null = null): number {
  if (fixed !== null) return capRows((kind && MIN_ROWS[kind]) || 1, fixed);
  return measured || (kind && MIN_ROWS[kind]) || Math.min(MAX_ROWS, fallback);
}

/**
 * Lage und Breite aus dem Raster übernehmen, die Höhe bleibt wie gespeichert (sie ergibt sich aus dem Inhalt).
 * Dieselben Einträge zurück, wenn sich nichts geändert hat, dann gibt es kein PUT.
 */
export function placeItems(items: DashboardLayoutItem[], layout: Layout): DashboardLayoutItem[] {
  let changed = false;
  const next = items.map((item) => {
    const match = layout.find((l) => l.i === itemKey(item));
    if (!match || (match.x === item.x && match.y === item.y && match.w === item.w)) return item;
    changed = true;
    return { ...item, x: match.x, y: match.y, w: match.w };
  });
  return changed ? next : items;
}

/**
 * Der Eintrag, den dieses Widget unter einer früheren Kennung seiner Erweiterung hatte (`legacy_ext_ids`,
 * nach einer Umbenennung): gleiche `widget_id`, `ext_id` aus der Liste. Bei mehreren gilt die Reihenfolge der
 * Liste. Ohne Umbenennung gibt es nie einen.
 */
function legacyItemFor(items: DashboardLayoutItem[], widget: WidgetOut): DashboardLayoutItem | undefined {
  for (const legacyId of widget.legacy_ext_ids ?? []) {
    const found = items.find((i) => i.ext_id === legacyId && i.widget_id === widget.id);
    if (found) return found;
  }
  return undefined;
}

/**
 * Platziert Widgets, die im Katalog (GET /widgets) auftauchen, aber noch nicht im
 * gespeicherten Layout stehen -- so "erscheinen" neu aktivierte Extensions von selbst
 * auf dem Dashboard, statt dass ein Nutzer sie erst manuell aus dem Widget-Picker
 * holen muss.
 *
 * Steht das Widget unter der früheren Kennung seiner (umbenannten) Erweiterung im Layout, wird es nicht
 * neu platziert, sondern der Eintrag übernommen (`{...alt, ext_id: neu}`): Position, Größe und ein
 * entferntes Widget (`config.hidden`) bleiben so, wie die Person sie eingerichtet hat. Der alte Eintrag
 * bleibt in den Daten stehen -- wer auf die alte Version zurückgeht, findet sein Dashboard unverändert --,
 * wird aber nicht gezeigt: der Katalog kennt ihn nicht (`widgetByKey`).
 */
function appendMissingWidgets(items: DashboardLayoutItem[], widgets: WidgetOut[]): DashboardLayoutItem[] {
  const existingKeys = new Set(items.map(itemKey));
  const missing = widgets.filter((w) => !existingKeys.has(itemKey({ ext_id: w.ext_id, widget_id: w.id })));
  if (missing.length === 0) return items;

  const carriedOver: DashboardLayoutItem[] = [];
  const fresh: WidgetOut[] = [];
  for (const w of missing) {
    const legacy = legacyItemFor(items, w);
    if (legacy) carriedOver.push({ ...legacy, ext_id: w.ext_id });
    else fresh.push(w);
  }

  let cursorX = 0;
  let cursorY = items.reduce((max, i) => Math.max(max, i.y + i.h), 0);
  const additions: DashboardLayoutItem[] = fresh.map((w) => {
    const size = sizeFor(w.view.kind, w.size.w, w.size.h);
    const width = size.w;
    if (cursorX + width > COLS) {
      cursorX = 0;
      cursorY += 1;
    }
    const item: DashboardLayoutItem = { widget_id: w.id, ext_id: w.ext_id, x: cursorX, y: cursorY, w: width, h: size.h, config: {} };
    cursorX += width;
    return item;
  });
  return [...items, ...carriedOver, ...additions];
}

/** Der Bearbeiten-Modus arbeitet auf einer Kopie, gespeichert wird sie erst mit „Fertig“. */
interface Draft {
  items: DashboardLayoutItem[];
  groups: DashboardGroupState[];
  /** Die Gruppen beim Start; „Fertig“ übernimmt nur Namen, die sich seitdem geändert haben. */
  startGroups: DashboardGroupState[];
}

export type Sized = { item: DashboardLayoutItem; size: { w: number; h: number }; min: { w: number; h: number } };

/**
 * Das Raster einer Gruppe, immer dicht von links oben gepackt in der gespeicherten Reihenfolge: Die Lage ist für die
 * ganze Seite gespeichert, in einer Gruppe blieben sonst die Lücken der anderen stehen. Ziehen ordnet damit um.
 */
export function gridLayoutFor(sized: Sized[]): Layout {
  const packed = packLayout(
    [...sized]
      .sort((a, b) => a.item.y - b.item.y || a.item.x - b.item.x)
      .map(({ item, size }) => ({ i: itemKey(item), w: size.w, h: size.h })),
  );
  return sized.map(({ item, size, min }) => {
    const pos = packed.get(itemKey(item))!;
    return { i: itemKey(item), x: pos.x, y: pos.y, w: size.w, h: size.h, minW: min.w };
  });
}

const ATTENTION_TEXT: Record<Attention, { title: string; label: string; dot: string }> = {
  danger: { title: "Hier ist etwas gestört", label: "Störung", dot: "bg-red-400" },
  warn: { title: "Hier gibt es eine Warnung", label: "Warnung", dot: "bg-amber-400" },
  unknown: { title: "Ein Widget hier lässt sich gerade nicht laden", label: "Nicht geladen", dot: "bg-white/40" },
};

function GroupHeader({ title, count, collapsed, attention, onToggle }: {
  title: string; count: number; collapsed: boolean; attention: Attention | null; onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      className="mb-2 flex w-full items-center gap-1.5 rounded-lg py-1 text-left text-sm font-medium text-white/80 hover:text-white"
    >
      {collapsed ? <ChevronRight size={15} aria-hidden /> : <ChevronDown size={15} aria-hidden />}
      <span>{title}</span>
      <span className="text-xs font-normal text-white/40">{count}</span>
      {/* Nur zugeklappt: Aufgeklappt zeigen die Karten selbst, was los ist. */}
      {collapsed && attention && (
        <span
          data-testid="group-attention"
          title={ATTENTION_TEXT[attention].title}
          className={`ml-1 inline-block h-2 w-2 rounded-full ${ATTENTION_TEXT[attention].dot}`}
        >
          <span className="sr-only">{ATTENTION_TEXT[attention].label}</span>
        </span>
      )}
    </button>
  );
}

function GroupNameField({ groupKey, title, count, onChange }: {
  groupKey: WidgetGroupKey; title: string; count: number; onChange: (title: string) => void;
}) {
  const fallback = defaultGroupTitle(groupKey);
  return (
    <div className="mb-2 flex items-center gap-2 py-0.5">
      <input
        type="text"
        aria-label={`Name der Gruppe ${fallback}`}
        value={title}
        placeholder={fallback}
        maxLength={60}
        onChange={(e) => onChange(e.target.value)}
        className={`${inputClass} max-w-xs py-1`}
      />
      <span className="text-xs text-white/40">{count}</span>
    </div>
  );
}

const BUTTON_CLASS = "rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20 disabled:opacity-50";

export function DashboardPage() {
  const { data: widgets, isLoading: widgetsLoading, error: widgetsError, refetch: refetchWidgets } = useWidgets();
  const {
    layout, isLoading: layoutLoading, error: layoutError, refetch: refetchLayout, fetchLatest, saveItems, saveGroups,
    saveLayout,
  } = useDashboardLayout();
  const { width, containerRef, mounted } = useContainerWidth();
  const [pendingItems, setPendingItems] = useState<DashboardLayoutItem[] | null>(null);
  const [pendingGroups, setPendingGroups] = useState<DashboardGroupState[] | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  // Auch die Tastatur ruht, solange „Fertig“ speichert, nicht nur die Maus.
  useEffect(() => {
    (containerRef.current as HTMLElement | null)?.toggleAttribute("inert", saving);
  }, [containerRef, saving]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const editing = draft !== null;
  // Der geklickte Knopf verschwindet beim Wechsel: Der Fokus geht zum Hinweis bzw. zurück auf „Bearbeiten“.
  const editHintRef = useRef<HTMLParagraphElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  useEffect(() => {
    if (editing === wasEditing.current) return;
    wasEditing.current = editing;
    (editing ? editHintRef : editButtonRef).current?.focus();
  }, [editing]);
  // Eine Karte, die die Gruppe wechselt, baut React in der neuen Gruppe neu auf, eine entfernte verschwindet: Ohne das
  // hier fiele der Fokus auf <body>.
  const groupSelects = useRef(new Map<string, HTMLSelectElement>());
  const focusAfterRender = useRef<(() => void) | null>(null);
  useEffect(() => {
    const focus = focusAfterRender.current;
    focusAfterRender.current = null;
    focus?.();
  });
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  // Ref statt State: der Guard muss INNERHALB desselben Effekt-Laufs wirken, bevor der
  // nächste Render committet -- React 18 StrictMode führt Mount-Effekte im Dev-Betrieb
  // zweimal aus (ein State-Guard kam beim zweiten Lauf noch zu spät und
  // löste ein doppeltes PUT /dashboard/layouts/{id} aus).
  // Gemerkt wird Layout plus Katalog: Kommt nach dem Zwischenspeicher der frische Katalog mit neuen Kacheln, gilt er noch.
  const autoPlacedForRef = useRef<string | null>(null);

  const savedItems = pendingItems ?? layout?.items ?? [];
  const items = draft?.items ?? savedItems;

  const widgetByKey = useMemo(() => {
    const map = new Map<string, WidgetOut>();
    for (const w of widgets ?? []) map.set(itemKey({ ext_id: w.ext_id, widget_id: w.id }), w);
    return map;
  }, [widgets]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- läuft bewusst nur, wenn sich Katalog, Layout-ID oder Bearbeiten-Modus ändern.
  useEffect(() => {
    // Im Bearbeiten-Modus nichts dazustellen: „Fertig“ speichert den Entwurf, der die neue Kachel nicht kennt. Nach „Fertig“
    // läuft der Effekt erneut und holt sie nach.
    if (!widgets || !layout || editing) return;
    const catalog = widgets.map((w) => itemKey({ ext_id: w.ext_id, widget_id: w.id })).sort().join("|");
    if (autoPlacedForRef.current === `${layout.id}:${catalog}`) return;
    autoPlacedForRef.current = `${layout.id}:${catalog}`;
    const merged = appendMissingWidgets(savedItems, widgets);
    if (merged !== savedItems) {
      setPendingItems(merged);
      void saveItems(merged);
    }
  }, [widgets, layout?.id, editing]);

  const placedItems = items.filter((item) => widgetByKey.has(itemKey(item)) && !isHiddenItem(item));

  // Dieselben Abfragen wie in den Karten (ein Schlüssel, eine Anfrage): Wer `meta.state = "not_configured"` meldet,
  // steht nicht auf der Startseite; „Widgets verwalten“ zeigt den Hinweis. Die Abfrage läuft weiter, nach dem
  // Einrichten erscheint das Widget von selbst.
  const placedWidgets = placedItems.map((item) => widgetByKey.get(itemKey(item))!);
  const dataQueries = useQueries({ queries: placedWidgets.map(widgetDataQuery) });
  const notConfigured = new Set(
    placedWidgets.filter((_, idx) => isNotConfigured(dataQueries[idx]?.data)).map((w) => itemKey({ ext_id: w.ext_id, widget_id: w.id })),
  );
  const visibleItems = placedItems.filter((item) => !notConfigured.has(itemKey(item)));

  // Von den Karten gemessene Rasterzeilen; nur im Speicher, nie im Layout gespeichert.
  const [measuredRows, setMeasuredRows] = useState<Record<string, number>>({});
  const reportRows = useCallback((key: string, rows: number) => {
    setMeasuredRows((prev) => (prev[key] === rows ? prev : { ...prev, [key]: rows }));
  }, []);

  const sized: Sized[] = visibleItems.map((item) => {
    const key = itemKey(item);
    const kind = widgetByKey.get(key)?.view.kind;
    const min = (kind && MIN_SIZE[kind]) || { w: 2, h: 2 };
    const grown = sizeFor(kind, item.w, item.h);
    return { item, size: { w: grown.w, h: rowsFor(kind, measuredRows[key], grown.h, fixedRowsOf(item.config)) }, min };
  });

  const storedGroups = pendingGroups ?? layout?.groups;
  const shownGroups = draft?.groups ?? storedGroups;
  const queryByKey = new Map(placedWidgets.map((w, idx) => [itemKey({ ext_id: w.ext_id, widget_id: w.id }), dataQueries[idx]]));
  const sections = orderedGroups(shownGroups)
    .map((group) => {
      const entries = sized.filter(({ item }) => groupOf(item.config, widgetByKey.get(itemKey(item))) === group.key);
      // Ein Ladefehler zählt mit: Eine zugeklappte Gruppe darf nicht ruhig wirken, nur weil nichts ankommt.
      const attention = worstAttention(entries.map(({ item }) => {
        const query = queryByKey.get(itemKey(item));
        return query?.isError ? "unknown" : attentionOf(widgetByKey.get(itemKey(item))!, query?.data);
      }));
      const ownTitle = shownGroups?.find((g) => g.key === group.key)?.title ?? "";
      // Im Bearbeiten-Modus stehen alle Gruppen offen; der gespeicherte Zustand bleibt im Entwurf unberührt.
      return { group: { ...group, collapsed: group.collapsed && !editing }, ownTitle, entries, attention };
    })
    .filter(({ entries }) => entries.length > 0);

  // Der neueste Stand, auch wenn zwei Klicks vor dem nächsten Rendern kommen: sonst gewänne nur der letzte.
  const groupsRef = useRef(storedGroups);
  groupsRef.current = storedGroups;
  // Sind alle eigenen Klicks gespeichert, gilt wieder der Stand vom Server, auch eine Änderung von einem anderen Gerät.
  const groupSavesRunning = useRef(0);
  function handleToggleGroup(key: DashboardGroupState["key"]) {
    const next = toggleGroup(groupsRef.current, key);
    groupsRef.current = next;
    setPendingGroups(next);
    groupSavesRunning.current += 1;
    void saveGroups(next)
      .catch(() => undefined)
      .finally(() => {
        groupSavesRunning.current -= 1;
        if (groupSavesRunning.current === 0) setPendingGroups(null);
      });
  }

  function startEditing() {
    setSaveError(null);
    // Die Namensfelder zeigen den Namen, den die Gruppe gerade trägt, auch den der Vorgabe.
    const groups = orderedGroups(storedGroups).map(({ key, title, collapsed }) => ({ key, title, collapsed }));
    setDraft({ items: savedItems, groups, startGroups: groups });
  }

  function cancelEditing() {
    setSaveError(null);
    setDraft(null);
  }

  // Eigene Klicks auf Gruppen, die noch gespeichert werden, kennt der Server vielleicht noch nicht.
  async function latestGroups() {
    return groupSavesRunning.current > 0 ? groupsRef.current : (await fetchLatest())?.groups;
  }

  // Alles in einer Anfrage: Klappt sie nicht, bleibt der Entwurf stehen und „Fertig“ lässt sich wiederholen.
  async function finishEditing() {
    if (!draft || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      // Ein anderes Gerät kann im Modus zugeklappt oder umbenannt haben: Die Gruppen gehen nur mit, wenn hier ein Name
      // geändert wurde, und dann auf dem frischen Stand vom Server.
      const groups = groupsRenamed(draft.startGroups, draft.groups)
        ? mergeDraftGroups(await latestGroups(), draft.startGroups, draft.groups)
        : undefined;
      await saveLayout(draft.items, groups);
      setPendingItems(draft.items);
      // Wie nach einem Klick auf eine Gruppe: Danach gilt der Stand vom Server.
      if (groupSavesRunning.current === 0) setPendingGroups(null);
      else if (groups) setPendingGroups(groups);
      setDraft(null);
    } catch (err) {
      setSaveError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  // Während „Fertig“ speichert, ginge jede weitere Änderung mit dem Entwurf verloren.
  function editItem(key: string, change: (item: DashboardLayoutItem) => DashboardLayoutItem) {
    if (saving) return;
    setDraft((d) => d && { ...d, items: d.items.map((item) => (itemKey(item) === key ? change(item) : item)) });
  }

  // Ein Pfeil auf dem geschlossenen Feld wechselt in manchen Browsern schon die Gruppe, der nächste soll weiter wirken.
  function moveToGroup(key: string, group: WidgetGroupKey) {
    if (saving) return;
    const others = sized
      .filter(({ item }) => itemKey(item) !== key && groupOf(item.config, widgetByKey.get(itemKey(item))) === group)
      .map(({ item }) => item);
    editItem(key, (item) => withGroup(item, group, others));
    focusAfterRender.current = () => groupSelects.current.get(key)?.focus();
  }

  function removeWidget(key: string) {
    if (saving) return;
    editItem(key, withoutWidget);
    // Der Hinweis steht über allen Gruppen: Wer weiter unten entfernt, bleibt an seiner Stelle.
    focusAfterRender.current = () => editHintRef.current?.focus({ preventScroll: true });
  }

  // Nur was die Person im Raster zieht, nicht jede Höhenänderung durch neue Daten.
  function handleDraftLayout(newLayout: Layout) {
    if (saving) return;
    setDraft((d) => {
      if (!d) return d;
      const merged = placeItems(d.items, newLayout);
      return merged === d.items ? d : { ...d, items: merged };
    });
  }

  function handlePickerChange(newItems: DashboardLayoutItem[]) {
    if (saving) return;
    if (editing) {
      setDraft((d) => d && { ...d, items: newItems });
      return;
    }
    setPendingItems(newItems);
    void saveItems(newItems);
  }

  const groupChoices = orderedGroups(shownGroups).map(({ key, title }) => ({ key, title }));
  function renderCard({ item, size, min }: Sized, grid: boolean) {
    const key = itemKey(item);
    const widget = widgetByKey.get(key)!;
    return (
      <WidgetCard
        widget={widget}
        onRows={(rows) => reportRows(key, rows)}
        fixedRows={fixedRowsOf(item.config)}
        draggable={editing && grid}
        edit={editing ? (
          <WidgetEditBar
            title={widget.title}
            size={sizeKeyOf(item.config)}
            wide={widthChoice(size.w, min.w)}
            showWidth={grid}
            group={groupOf(item.config, widget)}
            groups={groupChoices}
            groupRef={(el) => {
              if (el) groupSelects.current.set(key, el);
              else groupSelects.current.delete(key);
            }}
            onSize={(next) => editItem(key, (i) => withSize(i, next))}
            onWide={(wide) => editItem(key, (i) => withWidth(i, wide, min.w))}
            onGroup={(next) => moveToGroup(key, next)}
            onRemove={() => removeWidget(key)}
          />
        ) : undefined}
      />
    );
  }

  // Das gemessene <div> (containerRef) MUSS ab dem allerersten Render existieren:
  // `useContainerWidth` misst und hängt seinen ResizeObserver nur EINMAL beim Mount
  // an. Stand es erst nach einem Early-Return für den Ladezustand im DOM, fand der
  // Mount-Effekt kein Element -- das Raster blieb dauerhaft beim 1280px-Default und
  // schnitt auf schmaleren Bildschirmen die rechten Karten ab. Lade-
  // und Fehlerzustand deshalb INNERHALB des gemessenen Containers.
  const loading = widgetsLoading || layoutLoading;

  return (
    <div className="pb-10">
      <Cockpit />
      <div id="dashboard-widgets" className="scroll-mt-4 px-4 pt-10 sm:px-6">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold">Widgets</h2>
        {!widgetsLoading && !layoutLoading && layout && (
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <WidgetPickerDialog
              widgets={widgets ?? []}
              items={items}
              onChange={handlePickerChange}
              open={pickerOpen}
              onOpenChange={setPickerOpen}
              notConfigured={notConfigured}
              drafting={editing}
            />
            {editing ? (
              <>
                <button type="button" onClick={cancelEditing} disabled={saving} className={BUTTON_CLASS}>
                  Abbrechen
                </button>
                <button
                  type="button"
                  onClick={() => void finishEditing()}
                  disabled={saving}
                  className="accent-gradient inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-sm font-medium text-on-accent transition hover:bg-accent-strong disabled:opacity-50"
                >
                  Fertig
                </button>
              </>
            ) : visibleItems.length > 0 && (
              <button type="button" ref={editButtonRef} onClick={startEditing} className={BUTTON_CLASS}>
                Bearbeiten
              </button>
            )}
          </div>
        )}
      </div>
      {editing && (
        <p ref={editHintRef} tabIndex={-1} className="mb-3 text-sm text-white/60 outline-none" data-testid="edit-hint">
          {width < STACK_BELOW
            ? "Bearbeiten: Ändere Höhe und Gruppe der Widgets. Verschieben und Breite gehen erst auf einem größeren Bildschirm. „Fertig“ speichert, „Abbrechen“ verwirft."
            : "Bearbeiten: Ziehe Widgets am Griff, ändere Höhe, Breite und Gruppe. „Fertig“ speichert, „Abbrechen“ verwirft."}
        </p>
      )}
      {saveError && (
        <p role="alert" className="mb-3 text-sm text-red-400">
          Speichern hat nicht geklappt. Deine Änderungen sind noch da, versuche es mit „Fertig“ noch einmal.
          <span className="mt-0.5 block text-xs text-red-400/80">{saveError}</span>
        </p>
      )}
      {/* react-grid-layout bündelt eigene React-Typen (leicht abweichend von unserem
          installierten @types/react) -- strukturell dasselbe Ref-Objekt, daher der Cast. */}
      <div ref={containerRef as Ref<HTMLDivElement>} className={saving ? "pointer-events-none opacity-70" : undefined} aria-busy={saving}>
        {loading && <p className="text-sm opacity-60">Lade Dashboard …</p>}
        {!loading && !layout && (layoutError
          ? <LoadError what="Die Anordnung der Widgets" error={layoutError} onRetry={() => void refetchLayout()} />
          : <p className="text-sm text-red-400">Kein Dashboard-Layout gefunden.</p>)}
        {!loading && layout && widgetsError && (
          <LoadError what="Die Liste der Widgets" error={widgetsError} onRetry={() => void refetchWidgets()} />
        )}
        {!loading && layout && (
          <>
            {/* Der Picker steht bewusst AUSSERHALB des `visibleItems.length === 0`-Zweigs
                (in der Überschriftenzeile oben) -- sonst gäbe es keinen Weg
                zurück, sobald alle Widgets manuell entfernt wurden (siehe
                Modul-Docstring von WidgetPickerDialog.tsx). */}

            {placedItems.length === 0 && (widgets ?? []).length === 0 && !widgetsError && (
              <EmptyState
                icon="layout-dashboard"
                testId="dashboard-no-widgets"
                title="Noch keine Widgets"
                text={
                  canManageExtensions
                    ? "Widgets bringen die Module mit. Schalte unter Einstellungen → Erweiterungen ein Modul ein, dann erscheinen seine Widgets hier von selbst."
                    : "Widgets bringen die Module mit. Sobald ein Administrator ein Modul einschaltet, erscheinen seine Widgets hier von selbst."
                }
                action={canManageExtensions ? <ButtonLink to="/settings/extensions">Module ansehen</ButtonLink> : undefined}
              />
            )}
            {visibleItems.length === 0 && placedItems.length > 0 && (
              <EmptyState
                icon="layout-dashboard"
                testId="dashboard-all-not-configured"
                title="Noch nichts eingerichtet"
                text="Die Module deiner Widgets brauchen noch ihre Einstellungen. Unter „Widgets verwalten“ siehst du, welche, mit Link dorthin."
                action={
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="accent-gradient inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-sm font-medium text-on-accent transition hover:bg-accent-strong"
                  >
                    Widgets verwalten
                  </button>
                }
              />
            )}
            {placedItems.length === 0 && (widgets ?? []).length > 0 && (
              <EmptyState
                icon="layout-dashboard"
                testId="dashboard-no-visible-widgets"
                title="Keine Widgets auf dem Dashboard"
                text="Such dir aus, was hier erscheinen soll: Zahlen, Listen und Schnellzugriffe deiner Module."
                action={
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="accent-gradient inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-sm font-medium text-on-accent transition hover:bg-accent-strong"
                  >
                    Widgets auswählen
                  </button>
                }
              />
            )}

            {mounted && sections.map(({ group, ownTitle, entries, attention }) => (
              <section key={group.key} className="mb-5" data-testid={`widget-group-${group.key}`}>
                {editing ? (
                  <GroupNameField
                    groupKey={group.key}
                    title={ownTitle}
                    count={entries.length}
                    onChange={(title) => !saving && setDraft((d) => d && { ...d, groups: renameGroup(d.groups, group.key, title) })}
                  />
                ) : (
                  <GroupHeader
                    title={group.title}
                    count={entries.length}
                    collapsed={group.collapsed}
                    attention={attention}
                    onToggle={() => handleToggleGroup(group.key)}
                  />
                )}
                {!group.collapsed && width < STACK_BELOW && (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-testid="widget-stack">
                    {[...entries]
                      .sort((a, b) => a.item.y - b.item.y || a.item.x - b.item.x)
                      .map((entry) => (
                        <div key={itemKey(entry.item)} style={{ height: rowsToPx(entry.size.h) }}>
                          {renderCard(entry, false)}
                        </div>
                      ))}
                  </div>
                )}
                {!group.collapsed && width >= STACK_BELOW && (
                  <GridLayout
                    width={width}
                    gridConfig={{ cols: COLS, rowHeight: ROW_HEIGHT, margin: [ROW_GAP, ROW_GAP], containerPadding: null, maxRows: Infinity }}
                    layout={gridLayoutFor(entries)}
                    // Verschieben und Breite ziehen gibt es nur im Bearbeiten-Modus, das Ziehen nur am Griff im Kopf.
                    // Die Höhe folgt dem Inhalt oder der gewählten Größe, deshalb nur die Breite zum Ziehen.
                    dragConfig={{ enabled: editing, handle: ".widget-drag-handle", cancel: "a,button" }}
                    resizeConfig={{ enabled: editing, handles: ["e"] }}
                    onDragStop={handleDraftLayout}
                    onResizeStop={handleDraftLayout}
                  >
                    {entries.map((entry) => (
                      <div key={itemKey(entry.item)}>{renderCard(entry, true)}</div>
                    ))}
                  </GridLayout>
                )}
              </section>
            ))}
          </>
        )}
      </div>
      </div>
    </div>
  );
}
