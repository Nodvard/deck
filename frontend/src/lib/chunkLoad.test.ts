import { describe, expect, it, vi } from "vitest";

import { CHUNK_RELOAD_GUARD_MS, installChunkReload, loadOnce } from "./chunkLoad";

/**
 * Nach einem Deploy fehlen die alten, gehashten Chunks (xterm, noVNC). Der
 * Terminal-Loader merkte sich das abgelehnte Promise, "Neu verbinden" scheiterte bis
 * zum Neuladen. Und niemand hat die Seite neu geladen.
 */
describe("loadOnce", () => {
  it("teilt einen laufenden bzw. erfolgreichen Import", async () => {
    const load = vi.fn(async () => "modul");
    const get = loadOnce(load);
    const [a, b] = await Promise.all([get(), get()]);
    expect(a).toBe("modul");
    expect(b).toBe("modul");
    await get();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("merkt sich einen Fehlschlag NICHT: der naechste Aufruf laedt neu", async () => {
    const load = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch dynamically imported module"))
      .mockResolvedValueOnce("modul");
    const get = loadOnce(load);
    await expect(get()).rejects.toThrow("Failed to fetch");
    await expect(get()).resolves.toBe("modul");
    expect(load).toHaveBeenCalledTimes(2);
  });
});

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map<string, string>(Object.entries(initial));
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

describe("installChunkReload", () => {
  it("laedt bei vite:preloadError einmal neu", () => {
    const reload = vi.fn();
    const storage = memoryStorage();
    const uninstall = installChunkReload(window, { storage: () => storage, reload, now: () => 1_000_000 });
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).toHaveBeenCalledTimes(1);
    uninstall();
  });

  it("keine Endlosschleife: kurz nach einem Neuladen nicht noch einmal", () => {
    const reload = vi.fn();
    const storage = memoryStorage();
    let now = 1_000_000;
    const uninstall = installChunkReload(window, { storage: () => storage, reload, now: () => now });
    window.dispatchEvent(new Event("vite:preloadError"));
    now += 1_000;
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).toHaveBeenCalledTimes(1);
    now += CHUNK_RELOAD_GUARD_MS;
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).toHaveBeenCalledTimes(2);
    uninstall();
  });

  it("ohne nutzbaren sessionStorage wird nicht neu geladen (und nichts wirft)", () => {
    const reload = vi.fn();
    const uninstall = installChunkReload(window, {
      storage: () => {
        throw new DOMException("blockiert", "SecurityError");
      },
      reload,
    });
    expect(() => window.dispatchEvent(new Event("vite:preloadError"))).not.toThrow();
    expect(reload).not.toHaveBeenCalled();
    uninstall();
  });

  it("merkt sich die Sperre", () => {
    const storage = memoryStorage();
    const uninstall = installChunkReload(window, { storage: () => storage, reload: vi.fn(), now: () => 1_000_000 });
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(storage.getItem("nodvard-deck:chunk-reload-at")).toBe("1000000");
    uninstall();
  });

  it("eine Sperre unter dem neuen Namen gilt", () => {
    const reload = vi.fn();
    const storage = memoryStorage({ "nodvard-deck:chunk-reload-at": "1000000" });
    const uninstall = installChunkReload(window, { storage: () => storage, reload, now: () => 1_000_000 + 1_000 });
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).not.toHaveBeenCalled();
    uninstall();
  });


  it("abgemeldet: kein Neuladen mehr", () => {
    const reload = vi.fn();
    const storage = memoryStorage();
    installChunkReload(window, { storage: () => storage, reload })();
    window.dispatchEvent(new Event("vite:preloadError"));
    expect(reload).not.toHaveBeenCalled();
  });
});
