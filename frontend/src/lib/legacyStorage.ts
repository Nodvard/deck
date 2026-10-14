/**
 * Browser-Speicher: Schlüssel unter dem alten Namen (`lattice.…`) werden einmal unter den neuen (`nodvard-deck.…`)
 * übernommen, damit nichts verloren geht. `migrateLegacyStorage()` läuft einmal vor dem ersten Rendern (main.tsx);
 * `sessionStorage` wird nicht umgezogen (`lib/chunkLoad.ts` liest beide Namen).
 */

/**
 * Schlüssel in `localStorage`: alt -> neu. Neue Schlüssel beginnen mit `nodvard-deck.`; nur Ersatz
 * für einen alten kommt hierher.
 */
export const LOCAL_STORAGE_MIGRATIONS = [
  { from: "lattice.changelogSeen", to: "nodvard-deck.changelogSeen" },
  { from: "lattice.console.layout", to: "nodvard-deck.console.layout" },
] as const;

/** Kopiert alte Schlüssel unter ihren neuen Namen, wo diese fehlen. Wirft nie, auch nicht bei gesperrtem Speicher. */
export function migrateLegacyStorage(storage?: Storage): void {
  let store: Storage;
  try {
    store = storage ?? window.localStorage;
  } catch {
    return; // Zugriff auf den Speicher selbst gesperrt
  }
  for (const { from, to } of LOCAL_STORAGE_MIGRATIONS) {
    try {
      // Ein schon gesetzter neuer Wert gewinnt. Der alte Schlüssel bleibt stehen, damit ein Zurück auf das alte
      // Image seine Werte noch findet.
      if (store.getItem(to) !== null) continue;
      const value = store.getItem(from);
      if (value !== null) store.setItem(to, value);
    } catch {
      // Dieser Schlüssel geht nicht (Speicher voll, gesperrt): die übrigen trotzdem versuchen.
    }
  }
}
