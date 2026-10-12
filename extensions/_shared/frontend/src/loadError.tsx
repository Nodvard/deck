/** Ladefehler sichtbar machen statt einer leeren Liste.
 *
 * Vorher wurde aus einer gescheiterten Abfrage oft `[]`, und die Seite zeigte "Noch keine …" oder
 * "Keine …". Das liest sich wie eine Entwarnung, obwohl nur die Abfrage nicht geklappt hat. Hier
 * stehen der Abruf, der bei einem Fehler den Grund vom Server wirft, und die Anzeige dafür mit
 * "Erneut versuchen". Eigene Datei statt `ui.tsx`, damit nur die Bundles neu gebaut werden, die
 * sie benutzen. */

import { authedFetch, errorFromBody } from "./api";
import { Button } from "./ui";

export const UNREADABLE_ANSWER_TEXT = "Die Antwort des Servers ließ sich nicht lesen.";

/** GET mit Token-Erneuerung; wirft bei einem Fehler den Grund vom Server (`detail`), sonst "HTTP <status>". */
export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(path, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(errorFromBody(body, res.status));
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(UNREADABLE_ANSWER_TEXT);
  }
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** "<what> ließ sich gerade nicht laden: <error>" mit Knopf "Erneut versuchen". `compact` für eine
 * Liste in einer Karte, sonst ein Kasten über die ganze Breite. */
export function LoadError({ what, error, onRetry, hint, compact = false }: {
  what: string;
  error: string;
  onRetry?: () => void;
  hint?: string;
  compact?: boolean;
}): JSX.Element {
  if (compact) {
    return (
      <div role="alert" data-testid="load-error" className="text-sm">
        <p className="text-red-300">{what} ließ sich gerade nicht laden: {error}</p>
        {hint && <p className="text-xs text-white/50">{hint}</p>}
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
      <div className="min-w-0 flex-1">
        <p className="text-red-200">{what} ließ sich gerade nicht laden: {error}</p>
        {hint && <p className="text-xs text-white/55">{hint}</p>}
      </div>
      {onRetry && <Button small onClick={onRetry}>Erneut versuchen</Button>}
    </div>
  );
}
