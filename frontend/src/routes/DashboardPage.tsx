import { useEffect, useMemo, useRef, useState, type Ref } from "react";
import { GridLayout, useContainerWidth, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";

import { ButtonLink, EmptyState } from "../components/EmptyState";
import { useWidgets } from "../lib/catalog";
import { isHiddenItem, useDashboardLayout, type DashboardLayoutItem } from "../lib/dashboard";
import type { WidgetOut } from "../widgets/types";
import { useAuthStore } from "../state/auth";
import { WidgetCard } from "../widgets/WidgetCard";
import { WidgetPickerDialog } from "./WidgetPickerDialog";
import { Cockpit } from "./Cockpit";

const COLS = 12;
const ROW_HEIGHT = 90;
// Das Raster fuellt die Containerbreite, wird aber nie schmaler als das: bei 12
// Spalten und 12px Abstand ist eine w=2-Karte 2*(W-156)/12+12 breit -- unter ~960px
// wuerden die Standard-Karten (w=2) unter ~146px schrumpfen und ihren Inhalt kaum
// noch fassen. Darunter scrollt <main> (overflow-auto) waagerecht, statt die Karten
// zu quetschen. Bewusst keine responsive Spaltenzahl: das Layout wird pro Nutzer in
// 12er-Koordinaten gespeichert, und react-grid-layout wuerde bei weniger Spalten
// umsortieren und diese Umsortierung per onLayoutChange zurueckspeichern -- ein
// kurzer Blick vom Handy haette dann das Desktop-Layout ueberschrieben.
const MIN_GRID_WIDTH = 960;
// Handy: statt des (gespeicherten) 12er-Rasters eine einfache Spalte in derselben
// Reihenfolge -- ohne react-grid-layout, also ohne Ziehen und ohne Zurueckspeichern.
// So bleibt das Desktop-Layout unberuehrt (siehe oben).
const STACK_BELOW = 768;

/**
 * Mindestgroesse je View-Typ, damit schmale Widgets ihren Inhalt nicht quetschen: fast
 * alle Widgets waren 2 von 12 Spalten breit (~170 px) -- Listen mit Knoepfen wurden gequetscht und bekamen
 * Scrollbalken. Gilt auch fuer bereits gespeicherte Layouts: react-grid-layout bekommt
 * die angehobene Groesse (und schiebt Kollisionen nach unten), onLayoutChange speichert
 * das dann mit.
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
 * gebraucht, wenn alte, zu kleine Karten auf ihre Mindestgroesse wachsen: an ihren alten
 * x-Positionen wuerden sie kollidieren und react-grid-layout schoebe sie treppenartig
 * nach unten (live in der Vorschau gesehen: grosse Luecken).
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
 * Platziert Widgets, die im Katalog (GET /widgets) auftauchen, aber noch nicht im
 * gespeicherten Layout stehen -- so "erscheinen" neu aktivierte Extensions von selbst
 * auf dem Dashboard, statt dass ein Nutzer sie erst manuell aus dem Widget-Picker
 * holen muss.
 */
function appendMissingWidgets(items: DashboardLayoutItem[], widgets: WidgetOut[]): DashboardLayoutItem[] {
  const existingKeys = new Set(items.map(itemKey));
  const missing = widgets.filter((w) => !existingKeys.has(itemKey({ ext_id: w.ext_id, widget_id: w.id })));
  if (missing.length === 0) return items;

  let cursorX = 0;
  let cursorY = items.reduce((max, i) => Math.max(max, i.y + i.h), 0);
  const additions: DashboardLayoutItem[] = missing.map((w) => {
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
  return [...items, ...additions];
}

export function DashboardPage() {
  const { data: widgets, isLoading: widgetsLoading } = useWidgets();
  const { layout, isLoading: layoutLoading, saveItems } = useDashboardLayout();
  const { width, containerRef, mounted } = useContainerWidth();
  const [pendingItems, setPendingItems] = useState<DashboardLayoutItem[] | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  // Ref statt State: der Guard muss INNERHALB desselben Effekt-Laufs wirken, bevor der
  // naechste Render committet -- React 18 StrictMode fuehrt Mount-Effekte im Dev-Betrieb
  // zweimal aus (ein State-Guard kam beim zweiten Lauf noch zu spaet und
  // loeste ein doppeltes PUT /dashboard/layouts/{id} aus).
  const autoPlacedForRef = useRef<string | null>(null);

  const items = pendingItems ?? layout?.items ?? [];

  const widgetByKey = useMemo(() => {
    const map = new Map<string, WidgetOut>();
    for (const w of widgets ?? []) map.set(itemKey({ ext_id: w.ext_id, widget_id: w.id }), w);
    return map;
  }, [widgets]);

  // eslint-disable-next-line react-hooks/exhaustive-deps -- laeuft bewusst nur, wenn sich Katalog oder Layout-ID aendern.
  useEffect(() => {
    if (!widgets || !layout || autoPlacedForRef.current === layout.id) return;
    autoPlacedForRef.current = layout.id;
    const merged = appendMissingWidgets(items, widgets);
    if (merged !== items) {
      setPendingItems(merged);
      void saveItems(merged);
    }
  }, [widgets, layout?.id]);

  const visibleItems = items.filter((item) => widgetByKey.has(itemKey(item)) && !isHiddenItem(item));
  const sized = visibleItems.map((item) => {
    const kind = widgetByKey.get(itemKey(item))?.view.kind;
    const min = (kind && MIN_SIZE[kind]) || { w: 2, h: 2 };
    return { item, size: sizeFor(kind, item.w, item.h), min };
  });
  const grew = sized.some(({ item, size }) => size.w !== item.w || size.h !== item.h);
  const packed = grew
    ? packLayout(
        [...sized]
          .sort((a, b) => a.item.y - b.item.y || a.item.x - b.item.x)
          .map(({ item, size }) => ({ i: itemKey(item), w: size.w, h: size.h })),
      )
    : null;
  const rglLayout: Layout = sized.map(({ item, size, min }) => {
    const pos = packed?.get(itemKey(item)) ?? { x: item.x, y: item.y };
    return { i: itemKey(item), x: pos.x, y: pos.y, w: size.w, h: size.h, minW: min.w, minH: min.h };
  });

  function handleLayoutChange(newLayout: Layout) {
    const merged = items.map((item) => {
      const match = newLayout.find((l) => l.i === itemKey(item));
      return match ? { ...item, x: match.x, y: match.y, w: match.w, h: match.h } : item;
    });
    setPendingItems(merged);
    void saveItems(merged);
  }

  function handlePickerChange(newItems: DashboardLayoutItem[]) {
    setPendingItems(newItems);
    void saveItems(newItems);
  }

  // Das gemessene <div> (containerRef) MUSS ab dem allerersten Render existieren:
  // `useContainerWidth` misst und haengt seinen ResizeObserver nur EINMAL beim Mount
  // an. Stand es erst nach einem Early-Return fuer den Ladezustand im DOM, fand der
  // Mount-Effekt kein Element -- das Raster blieb dauerhaft beim 1280px-Default und
  // schnitt auf schmaleren Bildschirmen die rechten Karten ab. Lade-
  // und Fehlerzustand deshalb INNERHALB des gemessenen Containers.
  const loading = widgetsLoading || layoutLoading;

  return (
    <div className="pb-10">
      <Cockpit />
      <div id="dashboard-widgets" className="scroll-mt-4 px-4 pt-10 sm:px-6">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Widgets</h2>
        {!widgetsLoading && !layoutLoading && layout && (
          <div className="ml-auto">
            <WidgetPickerDialog widgets={widgets ?? []} items={items} onChange={handlePickerChange} open={pickerOpen} onOpenChange={setPickerOpen} />
          </div>
        )}
      </div>
      {/* react-grid-layout buendelt eigene React-Typen (leicht abweichend von unserem
          installierten @types/react) -- strukturell dasselbe Ref-Objekt, daher der Cast. */}
      <div ref={containerRef as Ref<HTMLDivElement>}>
        {loading && <p className="text-sm opacity-60">Lade Dashboard …</p>}
        {!loading && !layout && <p className="text-sm text-red-400">Kein Dashboard-Layout gefunden.</p>}
        {!loading && layout && (
          <>
            {/* Der Picker steht bewusst AUSSERHALB des `visibleItems.length === 0`-Zweigs
                (jetzt in der Ueberschriftenzeile oben) -- sonst gaebe es keinen Weg
                zurueck, sobald alle Widgets manuell entfernt wurden (siehe
                Modul-Docstring von WidgetPickerDialog.tsx). */}

            {visibleItems.length === 0 && (widgets ?? []).length === 0 && (
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
            {visibleItems.length === 0 && (widgets ?? []).length > 0 && (
              <EmptyState
                icon="layout-dashboard"
                testId="dashboard-no-visible-widgets"
                title="Keine Widgets auf dem Dashboard"
                text="Such dir aus, was hier erscheinen soll: Zahlen, Listen und Schnellzugriffe deiner Module."
                action={
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="accent-gradient inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-sm font-medium text-white shadow-md shadow-black/30 transition hover:brightness-110"
                  >
                    Widgets auswählen
                  </button>
                }
              />
            )}

            {mounted && visibleItems.length > 0 && width < STACK_BELOW && (
              <div className="flex flex-col gap-3" data-testid="widget-stack">
                {[...sized]
                  .sort((a, b) => a.item.y - b.item.y || a.item.x - b.item.x)
                  .map(({ item, size }) => (
                    <div key={itemKey(item)} style={{ height: Math.max(2, Math.min(size.h, 5)) * ROW_HEIGHT }}>
                      <WidgetCard widget={widgetByKey.get(itemKey(item))!} />
                    </div>
                  ))}
              </div>
            )}

            {mounted && visibleItems.length > 0 && width >= STACK_BELOW && (
              <GridLayout
                width={Math.max(width, MIN_GRID_WIDTH)}
                gridConfig={{ cols: COLS, rowHeight: ROW_HEIGHT, margin: [12, 12], containerPadding: null, maxRows: Infinity }}
                layout={rglLayout}
                onLayoutChange={handleLayoutChange}
              >
                {visibleItems.map((item) => (
                  <div key={itemKey(item)}>
                    <WidgetCard widget={widgetByKey.get(itemKey(item))!} />
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
