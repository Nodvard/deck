/**
 * Höhe der Widget-Karten in Stufen XS (1 Rasterzeile), S (2), M (3), L (5): die kleinste, in die der Inhalt passt, oder
 * genau die gewählte (`config.size`); was nicht passt, endet mit „+ x weitere“. Gespeichert wird nur die gewählte Höhe,
 * die gemessene nie (sonst löste jede neue Zeile ein PUT aus).
 */
export const ROW_HEIGHT = 90;
export const ROW_GAP = 12;
export const STAGES = [1, 2, 3, 5] as const;
export const MAX_ROWS = STAGES[STAGES.length - 1];

/** Die wählbaren Höhen in Rasterzeilen; „Automatisch“ ist kein Eintrag, sondern ein fehlendes `config.size`. */
export const SIZE_ROWS = { s: 2, m: 3, l: 5 } as const;
export type SizeKey = keyof typeof SIZE_ROWS;

const SIZE_KEYS = Object.keys(SIZE_ROWS);

export function sizeKeyOf(config: Record<string, unknown> | undefined): SizeKey | null {
  const size = config?.size;
  return typeof size === "string" && SIZE_KEYS.includes(size) ? (size as SizeKey) : null;
}

/** Gewählte Höhe in Rasterzeilen, `null` heißt automatisch. */
export function fixedRowsOf(config: Record<string, unknown> | undefined): number | null {
  const key = sizeKeyOf(config);
  return key ? SIZE_ROWS[key] : null;
}

/** Zahl und Tacho sind mindestens S (gleich hoch nebeneinander), höher nur, wenn der Titel umbricht. */
export const MIN_ROWS: Record<string, number> = { gauge: 2, stat: 2 };

/** Höhe der Zeile „+ x weitere“ (mt-2 plus eine Zeile mit leading-5). */
export const MORE_LINE_PX = 28;

export function rowsToPx(rows: number): number {
  return rows * ROW_HEIGHT + (rows - 1) * ROW_GAP;
}

/** Die größte Höhe einer Karte: die gewählte Größe, sonst L; nie unter `minRows`. */
export function capRows(minRows: number, fixedRows: number | null = null): number {
  return Math.max(fixedRows ?? MAX_ROWS, minRows);
}

/**
 * Kleinste Stufe ab `minRows`, in die `px` passt; `null`, wenn nicht einmal L reicht. Bei gewählter Größe gibt es nur
 * diese eine Stufe.
 */
export function stageFor(px: number, minRows = 1, fixedRows: number | null = null): number | null {
  if (fixedRows !== null) {
    const rows = capRows(minRows, fixedRows);
    return px <= rowsToPx(rows) ? rows : null;
  }
  return STAGES.find((rows) => rows >= minRows && px <= rowsToPx(rows)) ?? null;
}

/** Wie viele Zeilen ganz in `available` passen (Unterkanten von oben gemessen), mindestens eine. */
export function fitCount(bottoms: readonly number[], available: number): number {
  let count = 0;
  for (const bottom of bottoms) {
    if (bottom > available) break;
    count += 1;
  }
  return Math.max(1, count);
}
