export type Sortierrichtung = "ascending" | "descending";
export type Sortierung<K extends string> = { spalte: K; richtung: Sortierrichtung } | null;
type Sortierwert = string | number | null | undefined;

export function naechsteSortierung<K extends string>(aktuell: Sortierung<K>, spalte: K): Sortierung<K> {
  if (aktuell?.spalte !== spalte) return { spalte, richtung: "ascending" };
  return aktuell.richtung === "ascending" ? { spalte, richtung: "descending" } : null;
}

/** Fehlende Werte stehen in beiden Richtungen hinten; gleiche Werte behalten ihre Reihenfolge. */
export function sortieren<T>(zeilen: readonly T[], wert: (zeile: T) => Sortierwert, richtung: Sortierrichtung): T[] {
  const fehlt = (v: Sortierwert) => v == null || (typeof v === "number" && !Number.isFinite(v));
  return zeilen.map((zeile, index) => ({ zeile, index, wert: wert(zeile) }))
    .sort((a, b) => {
      const aFehlt = fehlt(a.wert), bFehlt = fehlt(b.wert);
      if (aFehlt || bFehlt) return aFehlt === bFehlt ? a.index - b.index : aFehlt ? 1 : -1;
      const vergleich = typeof a.wert === "number" && typeof b.wert === "number"
        ? a.wert - b.wert
        : String(a.wert).localeCompare(String(b.wert), "de");
      return (richtung === "ascending" ? vergleich : -vergleich) || a.index - b.index;
    })
    .map(({ zeile }) => zeile);
}
