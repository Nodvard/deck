import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("Sitzungsspeicher der Verbindungswarnung", () => {
  it("behält das Ausblenden beim Neuladen bei", async () => {
    vi.resetModules();
    const first = (await import("./connectionWarning")).useConnectionWarningStore;
    first.getState().dismiss();
    vi.resetModules();
    const reloaded = (await import("./connectionWarning")).useConnectionWarningStore;
    expect(reloaded.getState().dismissed).toBe(true);
    reloaded.getState().reset();
    vi.resetModules();
    const nextLogin = (await import("./connectionWarning")).useConnectionWarningStore;
    expect(nextLogin.getState().dismissed).toBe(false);
  });

  it("funktioniert bei gesperrtem Browserspeicher bis zum Neuladen", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("gesperrt"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("gesperrt"); });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("gesperrt"); });
    vi.resetModules();
    const store = (await import("./connectionWarning")).useConnectionWarningStore;
    expect(store.getState().dismissed).toBe(false);
    store.getState().dismiss();
    expect(store.getState().dismissed).toBe(true);
    store.getState().reset();
    expect(store.getState().dismissed).toBe(false);
  });
});
