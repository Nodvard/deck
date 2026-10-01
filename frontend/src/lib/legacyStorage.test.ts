import { afterEach, describe, expect, it, vi } from "vitest";

import { SEEN_KEY } from "./changelog";
import { LOCAL_STORAGE_MIGRATIONS, migrateLegacyStorage } from "./legacyStorage";
import { LAYOUT_STORAGE_KEY } from "../routes/ConsolePage";

const OLD_SEEN = "lattice.changelogSeen";
const NEW_SEEN = "nodvard-deck.changelogSeen";
const OLD_LAYOUT = "lattice.console.layout";
const NEW_LAYOUT = "nodvard-deck.console.layout";

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (k: string) => data.get(k) ?? null,
    key: (i: number) => [...data.keys()][i] ?? null,
    removeItem: (k: string) => void data.delete(k),
    setItem: (k: string, v: string) => void data.set(k, v),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("migrateLegacyStorage", () => {
  it("nur alt vorhanden: kopiert unter dem neuen Namen und laesst den alten stehen (Rollback)", () => {
    const store = memoryStorage({ [OLD_SEEN]: "0.5.0|2", [OLD_LAYOUT]: "us" });
    migrateLegacyStorage(store);
    expect(store.getItem(NEW_SEEN)).toBe("0.5.0|2");
    expect(store.getItem(NEW_LAYOUT)).toBe("us");
    expect(store.getItem(OLD_SEEN)).toBe("0.5.0|2");
    expect(store.getItem(OLD_LAYOUT)).toBe("us");
  });

  it("neuer Schluessel schon da: bleibt unveraendert, der alte auch", () => {
    const store = memoryStorage({ [OLD_SEEN]: "alt", [NEW_SEEN]: "neu", [OLD_LAYOUT]: "us", [NEW_LAYOUT]: "de" });
    migrateLegacyStorage(store);
    expect(store.getItem(NEW_SEEN)).toBe("neu");
    expect(store.getItem(NEW_LAYOUT)).toBe("de");
    expect(store.getItem(OLD_SEEN)).toBe("alt");
    expect(store.getItem(OLD_LAYOUT)).toBe("us");
  });

  it("leerer Speicher: es entsteht nichts", () => {
    const store = memoryStorage();
    migrateLegacyStorage(store);
    expect(store.length).toBe(0);
  });

  it("ein leerer Wert zaehlt als Wert und wird mitgenommen", () => {
    const store = memoryStorage({ [OLD_SEEN]: "" });
    migrateLegacyStorage(store);
    expect(store.getItem(NEW_SEEN)).toBe("");
  });

  it("zweimal ausgefuehrt: gleiches Ergebnis, ein inzwischen geaenderter neuer Wert bleibt", () => {
    const store = memoryStorage({ [OLD_SEEN]: "0.5.0|2" });
    migrateLegacyStorage(store);
    migrateLegacyStorage(store);
    expect(store.getItem(NEW_SEEN)).toBe("0.5.0|2");
    expect(store.length).toBe(2);

    store.setItem(NEW_SEEN, "0.6.0|0"); // der Nutzer hat inzwischen das neue Protokoll gesehen
    migrateLegacyStorage(store);
    expect(store.getItem(NEW_SEEN)).toBe("0.6.0|0");
    expect(store.getItem(OLD_SEEN)).toBe("0.5.0|2");
  });

  it("Speicher wirft beim Lesen: nichts wirft nach aussen", () => {
    const store = memoryStorage({ [OLD_SEEN]: "x" });
    vi.spyOn(store, "getItem").mockImplementation(() => {
      throw new DOMException("blockiert", "SecurityError");
    });
    expect(() => migrateLegacyStorage(store)).not.toThrow();
  });

  it("Speicher wirft beim Schreiben (voll): nichts wirft, der alte Wert bleibt", () => {
    const store = memoryStorage({ [OLD_SEEN]: "x" });
    vi.spyOn(store, "setItem").mockImplementation(() => {
      throw new DOMException("voll", "QuotaExceededError");
    });
    expect(() => migrateLegacyStorage(store)).not.toThrow();
    expect(store.getItem(OLD_SEEN)).toBe("x");
    expect(store.getItem(NEW_SEEN)).toBeNull();
  });

  it("scheitert ein Schluessel, werden die uebrigen trotzdem uebernommen", () => {
    const store = memoryStorage({ [OLD_SEEN]: "a", [OLD_LAYOUT]: "us" });
    const realSet = store.setItem.bind(store);
    vi.spyOn(store, "setItem").mockImplementation((k, v) => {
      if (k === NEW_SEEN) throw new DOMException("voll", "QuotaExceededError");
      realSet(k, v);
    });
    migrateLegacyStorage(store);
    expect(store.getItem(NEW_SEEN)).toBeNull();
    expect(store.getItem(NEW_LAYOUT)).toBe("us");
  });

  it("schon der Zugriff auf localStorage ist gesperrt (privates Fenster): nichts wirft", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("gesperrt", "SecurityError");
    });
    expect(() => migrateLegacyStorage()).not.toThrow();
  });

  it("ohne Angabe wirkt es auf den echten localStorage", () => {
    window.localStorage.setItem(OLD_LAYOUT, "us");
    migrateLegacyStorage();
    expect(window.localStorage.getItem(NEW_LAYOUT)).toBe("us");
    expect(window.localStorage.getItem(OLD_LAYOUT)).toBe("us");
  });
});

describe("Liste der Schluessel", () => {
  it("passt zu den Schluesseln, die der Code wirklich benutzt", () => {
    const targets = LOCAL_STORAGE_MIGRATIONS.map((m) => m.to) as string[];
    expect(targets).toContain(SEEN_KEY);
    expect(targets).toContain(LAYOUT_STORAGE_KEY);
    expect(SEEN_KEY).toBe(NEW_SEEN);
    expect(LAYOUT_STORAGE_KEY).toBe(NEW_LAYOUT);
  });

  it("alt = lattice.*, neu = nodvard-deck.*, jeweils mit demselben Rest", () => {
    for (const { from, to } of LOCAL_STORAGE_MIGRATIONS) {
      expect(/^lattice\./.test(from)).toBe(true);
      expect(to).toBe(from.replace(/^lattice\./, "nodvard-deck."));
    }
  });
});
