import { useQueries } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type Ref } from "react";
import { GridLayout, useContainerWidth, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";

import { ButtonLink, EmptyState } from "../components/EmptyState";
import { useWidgets } from "../lib/catalog";
import { isHiddenItem, useDashboardLayout, type DashboardLayoutItem } from "../lib/dashboard";
import type { WidgetOut } from "../widgets/types";
import { useAuthStore } from "../state/auth";
import { MAX_ROWS, MIN_ROWS, ROW_GAP, ROW_HEIGHT, rowsToPx } from "../widgets/fit";
import { isNotConfigured, widgetDataQuery } from "../widgets/query";
import { WidgetCard } from "../widgets/WidgetCard";
import { WidgetPickerDialog } from "./WidgetPickerDialog";
import { Cockpit } from "./Cockpit";

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

/**
 * Rasterzeilen einer Karte: gemessen (WidgetCard meldet die Stufe), bis zur Messung die Mindesthöhe (Zahl und Tacho:
 * S). Die gespeicherte Höhe zählt nicht mehr, gespeichert werden nur Lage und Breite.
 */
export function rowsFor(kind: string | undefined, measured: number | undefined, fallback: number): number {
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

export function DashboardPage() {
  const { data: widgets, isLoading: widgetsLoading } = useWidgets();
  const { layout, isLoading: layoutLoading, saveItems } = useDashboardLayout();
  const { width, containerRef, mounted } = useContainerWidth();
  const [pendingItems, setPendingItems] = useState<DashboardLayoutItem[] | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  // Ref statt State: der Guard muss INNERHALB desselben Effekt-Laufs wirken, bevor der
  // nächste Render committet -- React 18 StrictMode führt Mount-Effekte im Dev-Betrieb
  // zweimal aus (ein State-Guard kam beim zweiten Lauf noch zu spät und
  // löste ein doppeltes PUT /dashboard/layouts/{id} aus).
  const autoPlacedForRef = useRef<string | null>(null);

  const items = pendingItems ?? layout?.items ?? [];

  const widgetByKey = useMemo(() => {
    const map = new Map<string, WidgetOut>();
    for (const w of widgets ?? []) map.set(itemKey({ ext_id: w.ext_id, widget_id: w.id }), w);
    return map;
  }, [widgets]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- läuft bewusst nur, wenn sich Katalog oder Layout-ID ändern.
  useEffect(() => {
    if (!widgets || !layout || autoPlacedForRef.current === layout.id) return;
    autoPlacedForRef.current = layout.id;
    const merged = appendMissingWidgets(items, widgets);
    if (merged !== items) {
      setPendingItems(merged);
      void saveItems(merged);
    }
  }, [widgets, layout?.id]);

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

  const sized = visibleItems.map((item) => {
    const key = itemKey(item);
    const kind = widgetByKey.get(key)?.view.kind;
    const min = (kind && MIN_SIZE[kind]) || { w: 2, h: 2 };
    const grown = sizeFor(kind, item.w, item.h);
    return { item, size: { w: grown.w, h: rowsFor(kind, measuredRows[key], grown.h) }, min };
  });
  // Nur gewachsene Breiten packen neu; unterschiedliche Höhen gleicht das Raster selbst aus (es schiebt nach oben).
  const grew = sized.some(({ item, size }) => size.w !== item.w);
  const packed = grew
    ? packLayout(
        [...sized]
          .sort((a, b) => a.item.y - b.item.y || a.item.x - b.item.x)
          .map(({ item, size }) => ({ i: itemKey(item), w: size.w, h: size.h })),
      )
    : null;
  const rglLayout: Layout = sized.map(({ item, size, min }) => {
    const pos = packed?.get(itemKey(item)) ?? { x: item.x, y: item.y };
    return { i: itemKey(item), x: pos.x, y: pos.y, w: size.w, h: size.h, minW: min.w };
  });

  // Gespeichert wird nur, was die Person selbst tut (Ziehen, Breite ändern), nicht jede Höhenänderung durch neue
  // Daten, sonst löste jede neue Zeile in einer Liste ein PUT aus.
  function handleUserLayout(newLayout: Layout) {
    const merged = placeItems(items, newLayout);
    if (merged === items) return;
    setPendingItems(merged);
    void saveItems(merged);
  }

  function handlePickerChange(newItems: DashboardLayoutItem[]) {
    setPendingItems(newItems);
    void saveItems(newItems);
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
          <div className="ml-auto">
            <WidgetPickerDialog
              widgets={widgets ?? []}
              items={items}
              onChange={handlePickerChange}
              open={pickerOpen}
              onOpenChange={setPickerOpen}
              notConfigured={notConfigured}
            />
          </div>
        )}
      </div>
      {/* react-grid-layout bündelt eigene React-Typen (leicht abweichend von unserem
          installierten @types/react) -- strukturell dasselbe Ref-Objekt, daher der Cast. */}
      <div ref={containerRef as Ref<HTMLDivElement>}>
        {loading && <p className="text-sm opacity-60">Lade Dashboard …</p>}
        {!loading && !layout && <p className="text-sm text-red-400">Kein Dashboard-Layout gefunden.</p>}
        {!loading && layout && (
          <>
            {/* Der Picker steht bewusst AUSSERHALB des `visibleItems.length === 0`-Zweigs
                (in der Überschriftenzeile oben) -- sonst gäbe es keinen Weg
                zurück, sobald alle Widgets manuell entfernt wurden (siehe
                Modul-Docstring von WidgetPickerDialog.tsx). */}

            {placedItems.length === 0 && (widgets ?? []).length === 0 && (
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

            {mounted && visibleItems.length > 0 && width < STACK_BELOW && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-testid="widget-stack">
                {[...sized]
                  .sort((a, b) => a.item.y - b.item.y || a.item.x - b.item.x)
                  .map(({ item, size }) => (
                    <div key={itemKey(item)} style={{ height: rowsToPx(size.h) }}>
                      <WidgetCard widget={widgetByKey.get(itemKey(item))!} onRows={(rows) => reportRows(itemKey(item), rows)} />
                    </div>
                  ))}
              </div>
            )}

            {mounted && visibleItems.length > 0 && width >= STACK_BELOW && (
              <GridLayout
                width={width}
                gridConfig={{ cols: COLS, rowHeight: ROW_HEIGHT, margin: [ROW_GAP, ROW_GAP], containerPadding: null, maxRows: Infinity }}
                layout={rglLayout}
                // Ziehen nur am Griff im Kopf (nicht ein Verrutscher in einer Liste); der Kopf selbst ist der Link zur Modulseite.
                // Die Höhe folgt dem Inhalt, deshalb nur die Breite zum Ziehen.
                dragConfig={{ handle: ".widget-drag-handle", cancel: "a,button" }}
                resizeConfig={{ handles: ["e"] }}
                onDragStop={handleUserLayout}
                onResizeStop={handleUserLayout}
              >
                {visibleItems.map((item) => (
                  <div key={itemKey(item)}>
                    <WidgetCard widget={widgetByKey.get(itemKey(item))!} onRows={(rows) => reportRows(itemKey(item), rows)} draggable />
                  </div>
                ))}
              </GridLayout>
            )}
          </>
        )}
      </div>
      </div>
    </div>
  );
}
