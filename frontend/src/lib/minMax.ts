/** Kleinster und größter endlicher Wert per Schleife (kein Spread, daher auch bei sehr langen Listen sicher); `null`, wenn keiner endlich ist. */
export function minMax(values: Iterable<number | null | undefined>): { min: number; max: number } | null {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (typeof v !== "number" || !Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return min > max ? null : { min, max };
}
