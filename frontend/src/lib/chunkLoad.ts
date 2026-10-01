/**
 * Nachgeladene Chunks nach einem Deploy.
 *
 * xterm (TerminalPage) und noVNC (ConsolePage) werden per `import()` nachgeladen und
 * liegen als eigene Dateien mit Hash im Namen unter /assets. Nach einem Update gibt
 * es im Container nur noch die NEUEN Dateien -- ein Tab, der vor dem Deploy offen war,
 * fordert aber die alten Namen an und scheitert ("Failed to fetch dynamically
 * imported module"). Abhilfe an zwei Stellen:
 *
 * - `loadOnce`: einen Import fuer alle Aufrufer teilen, einen FEHLSCHLAG aber nicht
 *   merken -- sonst scheitert "Neu verbinden" bis zum Neuladen am gespeicherten
 *   abgelehnten Promise.
 * - `installChunkReload`: Vite meldet einen gescheiterten Chunk-Import als
 *   `vite:preloadError` am window. Dann die Seite EINMAL neu laden: index.html ist
 *   `no-cache` (main.py::_cache_policy_for) und verweist auf die neuen Chunks.
 */

/** Import einmal laden und teilen; nach einem Fehlschlag laedt der naechste Aufruf neu. */
export function loadOnce<T>(load: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | null = null;
  return () => {
    pending ??= load().catch((err: unknown) => {
      pending = null;
      throw err;
    });
    return pending;
  };
}

const RELOAD_KEY = "nodvard-deck:chunk-reload-at";
/** Alter Name, gilt weiter (Uebergang): ein Tab, der vor einem Update schon neu geladen hat, kennt nur ihn. */
const LEGACY_RELOAD_KEY = "lattice:chunk-reload-at";
/** So lange nach einem automatischen Neuladen wird nicht noch einmal neu geladen. */
export const CHUNK_RELOAD_GUARD_MS = 60_000;

interface ChunkReloadOptions {
  storage?: () => Storage;
  reload?: () => void;
  now?: () => number;
}

/**
 * Bei `vite:preloadError` einmal neu laden. Die Sperre ueber sessionStorage verhindert
 * eine Endlosschleife, falls der Chunk auch nach dem Neuladen fehlt (dann bleibt die
 * normale Fehlermeldung der Seite stehen). Ohne nutzbaren sessionStorage gibt es keine
 * Sperre -- dann lieber gar nicht automatisch neu laden.
 */
export function installChunkReload(
  target: Pick<Window, "addEventListener" | "removeEventListener"> = window,
  options: ChunkReloadOptions = {},
): () => void {
  const storage = options.storage ?? (() => window.sessionStorage);
  const reload = options.reload ?? (() => window.location.reload());
  const now = options.now ?? Date.now;

  const onPreloadError = () => {
    try {
      const store = storage();
      // Beide Namen lesen (der juengere gilt): hat ein Tab mit dem alten Build kurz vorher schon
      // neu geladen, steht die Sperre nur unter dem alten Namen. Und beide schreiben, damit auch
      // ein zurueckgerollter alter Build sie sieht.
      const last = Math.max(Number(store.getItem(RELOAD_KEY)) || 0, Number(store.getItem(LEGACY_RELOAD_KEY)) || 0);
      if (Math.abs(now() - last) < CHUNK_RELOAD_GUARD_MS) return;
      store.setItem(RELOAD_KEY, String(now()));
      try {
        store.setItem(LEGACY_RELOAD_KEY, String(now()));
      } catch {
        // Nur Zugabe fuer den Rollback; die Sperre unter dem neuen Namen steht.
      }
    } catch {
      return;
    }
    reload();
  };

  target.addEventListener("vite:preloadError", onPreloadError);
  return () => target.removeEventListener("vite:preloadError", onPreloadError);
}
