/**
 * Ladefehler sichtbar machen statt eines Leerzustands. Aus einer gescheiterten Abfrage wurde sonst
 * "Noch keine …" oder "nicht gefunden" -- das liest sich wie eine Auskunft, dabei hat nur die Abfrage
 * nicht geklappt. Gegenstueck zu `LoadError` im Erweiterungs-UI-Kit
 * (extensions/_shared/frontend/src/loadError.tsx) -- gleiche Optik und gleicher Wortlaut.
 */
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function LoadError({ what, error, onRetry, compact = false }: {
  /** Subjekt in der Einzahl: "Der Server", "Die Liste der Werkzeuge". */
  what: string;
  error: unknown;
  onRetry?: () => void;
  /** Schmal, ohne Kasten (in einer Karte oder unter einer Ueberschrift). */
  compact?: boolean;
}) {
  const text = `${what} ließ sich gerade nicht laden: ${errorMessage(error)}`;
  if (compact) {
    return (
      <div role="alert" data-testid="load-error" className="text-sm">
        <p className="text-red-300">{text}</p>
        {onRetry && (
          <button type="button" onClick={onRetry} className="mt-1 text-xs text-white/70 underline underline-offset-2 hover:text-white">
            Erneut versuchen
          </button>
        )}
      </div>
    );
  }
  return (
    <div role="alert" data-testid="load-error" className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm">
      <p className="min-w-0 flex-1 text-red-200">{text}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="rounded-lg bg-white/10 px-2.5 py-1 text-xs font-medium hover:bg-white/15">
          Erneut versuchen
        </button>
      )}
    </div>
  );
}
