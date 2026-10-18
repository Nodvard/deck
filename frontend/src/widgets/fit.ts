/**
 * Höhe der Widget-Karten nach Inhalt: feste Stufen XS (1 Rasterzeile), S (2), M (3), L (5). Die Karte misst ihren
 * Inhalt und nimmt die kleinste Stufe, die passt; passt er nicht in L, endet die Liste an der letzten ganzen Zeile und
 * darunter steht „+ x weitere“. Die gemessene Höhe wird nicht gespeichert (sonst löste jede neue Zeile ein PUT aus).
 */
export const ROW_HEIGHT = 90;
export const ROW_GAP = 12;
export const STAGES = [1, 2, 3, 5] as const;
export const MAX_ROWS = STAGES[STAGES.length - 1];

/** Zahl und Tacho sind mindestens S (gleich hoch nebeneinander), höher nur, wenn der Titel umbricht. */
export const MIN_ROWS: Record<string, number> = { gauge: 2, stat: 2 };

/** Höhe der Zeile „+ x weitere“ (mt-2 plus eine Zeile mit leading-5). */
export const MORE_LINE_PX = 28;

export function rowsToPx(rows: number): number {
  return rows * ROW_HEIGHT + (rows - 1) * ROW_GAP;
}

/** Kleinste Stufe ab `minRows`, in die `px` passt; `null`, wenn nicht einmal L reicht. */
export function stageFor(px: number, minRows = 1): number | null {
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
