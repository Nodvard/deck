import { afterEach, describe, expect, it } from "vitest";

import { CUSTOM_CSS_URL, customCssDisabled, enableCustomCss, loadCustomCss } from "./customCss";

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() { return data.size; },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => { data.delete(k); },
    setItem: (k, v) => { data.set(k, v); },
  };
}

afterEach(() => {
  document.getElementById("custom-css")?.remove();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("eigenes CSS", () => {
  it("hängt die Datei als letztes Stylesheet ein und lädt sie nach dem Speichern neu", () => {
    loadCustomCss();
    const link = document.getElementById("custom-css") as HTMLLinkElement;
    expect(link.rel).toBe("stylesheet");
    expect(link.getAttribute("href")).toBe(CUSTOM_CSS_URL);
    expect(document.head.lastElementChild).toBe(link);
    loadCustomCss(42);
    expect(document.querySelectorAll("#custom-css")).toHaveLength(1);
    expect(link.getAttribute("href")).toBe(`${CUSTOM_CSS_URL}?v=42`);
  });

  it("?ohne-css=1 lässt es weg, auch nach dem Weiterklicken im selben Tab", () => {
    const storage = memoryStorage();
    expect(customCssDisabled("?ohne-css=1", storage)).toBe(true);
    expect(customCssDisabled("", storage)).toBe(true);
    expect(customCssDisabled("", memoryStorage())).toBe(false);

    window.history.replaceState(null, "", "/?ohne-css=1");
    loadCustomCss();
    expect(document.getElementById("custom-css")).toBeNull();
  });

  it("„Wieder einschalten“ hebt den Notausgang auf, auch in der Adresse, und lädt das CSS", () => {
    window.history.replaceState(null, "", "/settings/appearance?ohne-css=1&x=2#oben");
    expect(customCssDisabled()).toBe(true);
    enableCustomCss();
    expect(window.location.search).toBe("?x=2");
    expect(window.location.hash).toBe("#oben");
    expect(customCssDisabled()).toBe(false);
    expect(document.getElementById("custom-css")?.getAttribute("href")).toMatch(/^\/api\/v1\/branding\/custom\.css\?v=\d+$/);
  });
});
