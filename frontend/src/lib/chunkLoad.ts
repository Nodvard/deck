/**
 * Nachgeladene Chunks nach einem Deploy: xterm und noVNC liegen als eigene Dateien mit Hash im Namen unter /assets,
 * ein vor dem Update geöffneter Tab fordert aber die alten Namen an und scheitert. Abhilfe: `loadOnce` und
 * `installChunkReload`.
 */

/** Import einmal laden und teilen; nach einem Fehlschlag lädt der nächste Aufruf neu (sonst scheitert "Neu verbinden"
 * bis zum Neuladen am gespeicherten abgelehnten Promise). */
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
/** So lange nach einem automatischen Neuladen wird nicht noch einmal neu geladen. */
export const CHUNK_RELOAD_GUARD_MS = 60_000;

interface ChunkReloadOptions {
  storage?: () => Storage;
  reload?: () => void;
  now?: () => number;
}

/**
 * Bei `vite:preloadError` (gescheiterter Chunk-Import) einmal neu laden: `index.html` ist `no-cache` und verweist auf
 * die neuen Chunks. Die Sperre über sessionStorage verhindert eine Endlosschleife, falls der Chunk danach noch fehlt;
 * ohne nutzbaren sessionStorage gibt es keine Sperre, dann wird lieber gar nicht automatisch neu geladen.
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
      const last = Number(store.getItem(RELOAD_KEY)) || 0;
      if (Math.abs(now() - last) < CHUNK_RELOAD_GUARD_MS) return;
      store.setItem(RELOAD_KEY, String(now()));
    } catch {
      return;
    }
    reload();
  };

  target.addEventListener("vite:preloadError", onPreloadError);
  return () => target.removeEventListener("vite:preloadError", onPreloadError);
}
