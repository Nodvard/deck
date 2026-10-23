/** Änderungen einzelner Einträge im Bearbeiten-Modus der Startseite; jede gibt einen neuen Eintrag zurück. */
import type { DashboardLayoutItem } from "../lib/dashboard";
import type { SizeKey } from "./fit";
import type { WidgetGroupKey } from "./types";

/** Spalten des Rasters und einer breiten Karte. */
export const GRID_COLS = 12;
export const WIDE_COLS = 8;

/** Schmal heißt die Mindestbreite des Typs, mindestens 4 Spalten. */
export function narrowCols(minW: number): number {
  return Math.max(4, minW);
}

/** `true` bei genau breit, `false` bei schmal oder schmaler, `null` bei einer gezogenen Breite dazwischen oder darüber. */
export function widthChoice(cols: number, minW: number): boolean | null {
  if (cols === WIDE_COLS) return true;
  return cols <= narrowCols(minW) ? false : null;
}

export function withSize(item: DashboardLayoutItem, size: SizeKey | null): DashboardLayoutItem {
  const { size: _old, ...config } = item.config;
  return { ...item, config: size ? { ...config, size } : config };
}

export function withWidth(item: DashboardLayoutItem, wide: boolean, minW: number): DashboardLayoutItem {
  const w = wide ? WIDE_COLS : narrowCols(minW);
  // Die App liest dieselben Werte: Die Karte darf nicht über die zwölfte Spalte hinausragen.
  return { ...item, w, x: Math.min(item.x, GRID_COLS - w) };
}

/** Setzt die Karte ans Ende der Gruppe (`others` sind die übrigen Karten dort), nicht mitten zwischen sie. */
export function withGroup(item: DashboardLayoutItem, group: WidgetGroupKey, others: DashboardLayoutItem[]): DashboardLayoutItem {
  const y = others.reduce((max, other) => Math.max(max, other.y + 1), 0);
  return { ...item, x: 0, y, config: { ...item.config, group } };
}

export function withoutWidget(item: DashboardLayoutItem): DashboardLayoutItem {
  return { ...item, config: { ...item.config, hidden: true } };
}
