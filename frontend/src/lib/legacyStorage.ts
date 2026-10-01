/**
 * Browser-Speicher: Schluessel unter dem alten Namen (`lattice.…`) werden einmal unter den
 * neuen (`nodvard-deck.…`) uebernommen, damit nichts verloren geht (z. B. "Aenderungsprotokoll
 * schon gesehen", gemerkte Konsolen-Ansicht).
 *
 * Regeln (docs/02-EXTENSION-API.md, Uebergang):
 * - `migrateLegacyStorage()` laeuft einmal vor dem ersten Rendern (main.tsx).
 * - Kopiert wird nur, wenn der neue Schluessel noch fehlt: ein schon gesetzter neuer Wert gewinnt.
 * - Der alte Schluessel wird NICHT geloescht, damit ein Zurueck auf das alte Image seine Werte
 *   noch findet. Aufgeraeumt wird erst in einer spaeteren Version.
 * - Idempotent und ohne Wirkung, wenn der Speicher fehlt oder wirft (privates Fenster,
 *   gesperrte Website-Daten): dann passiert einfach nichts.
 *
 * sessionStorage wird nicht umgezogen: `lib/chunkLoad.ts` liest dort beide Namen.
 */

/** Schluessel in `localStorage`: alt -> neu. Neue Schluessel beginnen mit `nodvard-deck.`; nur Ersatz fuer einen alten kommt hierher. */
export const LOCAL_STORAGE_MIGRATIONS = [
  { from: "lattice.changelogSeen", to: "nodvard-deck.changelogSeen" },
  { from: "lattice.console.layout", to: "nodvard-deck.console.layout" },
] as const;

/** Kopiert alte Schluessel unter ihren neuen Namen, wo diese fehlen. Wirft nie. */
export function migrateLegacyStorage(storage?: Storage): void {
  let store: Storage;
  try {
    store = storage ?? window.localStorage;
  } catch {
    return; // Zugriff auf den Speicher selbst gesperrt
  }
  for (const { from, to } of LOCAL_STORAGE_MIGRATIONS) {
    try {
      if (store.getItem(to) !== null) continue;
      const value = store.getItem(from);
      if (value !== null) store.setItem(to, value);
    } catch {
      // Dieser Schluessel geht nicht (Speicher voll, gesperrt): die uebrigen trotzdem versuchen.
    }
  }
}
