import { describe, expect, it } from "vitest";

import { APP_ICON_NAMES, appIconComponent, isAppIconName } from "./appIcons";
import {
  bodyFromForm,
  EMPTY_FORM,
  existingGroups,
  FILTER_ALL,
  FILTER_DETECTED,
  FILTER_UNGROUPED,
  filterApps,
  groupChips,
  groupFilter,
  isEmoji,
  normalizeUrl,
  safeHref,
  urlHost,
  validateAppForm,
  validateUrl,
} from "./apps";
import type { AppTileOut } from "./overview";

const tile = (over: Partial<AppTileOut> & { id: string; name: string }): AppTileOut => ({
  source: "custom", url: "http://192.168.2.1", host: null, host_id: null, state: null, tone: null, image: null,
  icon: null, color: null, group: null, open_in_new_tab: true, sort_order: 0, ...over,
});

describe("normalizeUrl", () => {
  it("ergänzt http:// bei einer Adresse ohne Anfang, auch mit Port und Pfad", () => {
    expect(normalizeUrl("192.168.2.1")).toBe("http://192.168.2.1");
    expect(normalizeUrl("  router.fritz.box/admin ")).toBe("http://router.fritz.box/admin");
    expect(normalizeUrl("nas.local:5000")).toBe("http://nas.local:5000");
    expect(normalizeUrl("localhost:8080/x")).toBe("http://localhost:8080/x");
  });

  it("lässt http(s) unverändert und fasst fremde Schemata nicht an", () => {
    expect(normalizeUrl("https://pihole.lan/admin")).toBe("https://pihole.lan/admin");
    expect(normalizeUrl("HTTP://Host")).toBe("HTTP://Host");
    for (const bad of ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://nas/", "mailto:a@b.de", "//evil.example/"]) {
      expect(normalizeUrl(bad)).toBe(bad);
    }
    expect(normalizeUrl("   ")).toBe("");
  });
});

describe("validateUrl", () => {
  it.each([
    "http://192.168.2.1",
    "https://router.fritz.box/",
    "http://nas.local:5000/webman?x=1#top",
    "http://[fe80::1]:8080/",
    "http://docker_host:3000",
    "HTTP://Example.COM",
  ])("nimmt %s an", (url) => {
    expect(validateUrl(url)).toBeNull();
  });

  it.each([
    ["javascript:alert(1)", "http://"],
    ["data:text/html;base64,AAAA", "http://"],
    ["file:///etc/passwd", "http://"],
    ["ftp://nas/", "http://"],
    ["192.168.2.1", "http://"],
    ["", "Adresse"],
    ["http://", "Rechnernamen"],
    ["http://:8080", "Rechnernamen"],
    ["http://user:pass@host/", "Benutzername"],
    ["https://google.com@evil.example/", "Benutzername"],
    ["http://exa mple.org/", "Leerzeichen"],
    ["http://example.org\\@evil.example/", "Rückwärts"],
    ["http://host:99999/", "Port"],
    ["http://host:abc/", "Port"],
  ])("lehnt %s ab", (url, part) => {
    expect(validateUrl(url)).toContain(part);
  });

  it("begrenzt die Länge", () => {
    expect(validateUrl(`http://example.org/${"a".repeat(1100)}`)).toContain("zu lang");
  });
});

describe("safeHref und urlHost", () => {
  it("gibt nur http(s) als Link frei", () => {
    expect(safeHref("http://192.168.2.1/x")).toBe("http://192.168.2.1/x");
    expect(safeHref("https://a.example")).toBe("https://a.example");
    expect(safeHref("javascript:alert(1)")).toBeUndefined();
    expect(safeHref("data:text/html,x")).toBeUndefined();
    expect(safeHref("//evil.example")).toBeUndefined();
    expect(safeHref("http://")).toBeUndefined();
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref("")).toBeUndefined();
  });

  it("zeigt Rechner und Port als Untertitel", () => {
    expect(urlHost("http://192.168.2.1:8080/admin")).toBe("192.168.2.1:8080");
    expect(urlHost("https://nas.local/")).toBe("nas.local");
    expect(urlHost(undefined)).toBe("");
    expect(urlHost("kaputt")).toBe("");
  });
});

describe("isEmoji", () => {
  const joined = String.fromCodePoint(0x1f9d1, 0x200d, 0x1f4bb); // Person am Computer
  const info = String.fromCodePoint(0x2139, 0xfe0f);
  it("nimmt einzelne Emoji an, auch zusammengesetzte", () => {
    for (const ok of ["🚀", "🏠", "🇩🇪", "👍🏽", joined, info, "☁️"]) expect(isEmoji(ok)).toBe(true);
  });

  it("lehnt Text, Adressen und Leeres ab", () => {
    for (const bad of ["", "a", "1", "abc", "🚀a", "🚀 🚀", "http://x.example/p.png", String.fromCodePoint(0x200d), "🚀".repeat(13)]) {
      expect(isEmoji(bad)).toBe(false);
    }
  });

  it("lehnt nicht vergebene Zeichen außerhalb der Emoji-Blöcke und Füllzeichen ab (wie das Backend)", () => {
    for (const code of [0x10ffff, 0x3ffff, 0xe0000, 0x1ffff, 0x3164, 0x2800]) {
      expect(isEmoji(String.fromCodePoint(code))).toBe(false);
    }
    expect(isEmoji(String.fromCodePoint(0x10ffff, 0xfe0f))).toBe(false);
  });

  it("nimmt ein (noch) nicht vergebenes Zeichen in den Emoji-Blöcken an: neuere Emoji als das Wissen des Browsers", () => {
    expect(isEmoji(String.fromCodePoint(0x1faff))).toBe(true);
  });
});

describe("validateAppForm", () => {
  const ok = { ...EMPTY_FORM, name: "Router", url: "192.168.2.1" };

  it("ist für Name und Adresse in Ordnung (Adresse wird ergänzt)", () => {
    expect(validateAppForm(ok)).toEqual({});
  });

  it("meldet alle Fehler auf einmal, mit verständlichen Sätzen", () => {
    const errors = validateAppForm({ ...EMPTY_FORM, name: "  ", url: "javascript:alert(1)", icon: "gibt-es-nicht", color: "red", group: "g".repeat(41) });
    expect(Object.keys(errors).sort()).toEqual(["color", "group", "icon", "name", "url"]);
    expect(errors.name).toBe("Bitte gib einen Namen an.");
    expect(errors.url).toBe("Die Adresse muss mit http:// oder https:// beginnen.");
    expect(errors.icon).toContain("Symbol");
    expect(errors.color).toContain("Farbe");
    expect(errors.group).toContain("zu lang");
  });

  it("begrenzt den Namen und lehnt Steuerzeichen ab", () => {
    expect(validateAppForm({ ...ok, name: "N".repeat(61) }).name).toContain("zu lang");
    expect(validateAppForm({ ...ok, name: "Zeile\neins" }).name).toContain("Steuerzeichen");
  });

  it("lehnt Zeilen- und Absatztrenner, Sonderleerzeichen und nicht vergebene Zeichen in Name und Gruppe ab", () => {
    for (const code of [0x2028, 0x2029, 0xa0, 0x3000, 0x2003, 0x202f, 0x200b, 0xe0000, 0x10ffff, 0x3ffff, 0x1ffff]) {
      const bad = `Router${String.fromCodePoint(code)}Admin`;
      expect(validateAppForm({ ...ok, name: bad }).name, `Name mit U+${code.toString(16)}`).toContain("Steuerzeichen");
      expect(validateAppForm({ ...ok, group: bad }).group, `Gruppe mit U+${code.toString(16)}`).toContain("Steuerzeichen");
    }
  });

  it("verlangt im Namen ein sichtbares Zeichen (Füllzeichen und Verbinder allein sind kein Name)", () => {
    for (const code of [0x3164, 0x115f, 0xffa0, 0x200d, 0xfe0f, 0x2800, 0x34f]) {
      expect(validateAppForm({ ...ok, name: String.fromCodePoint(code) }).name, `U+${code.toString(16)}`).toBe("Bitte gib einen Namen an.");
    }
    expect(validateAppForm({ ...ok, name: String.fromCodePoint(0x115f, 0x1160) }).name).toBe("Bitte gib einen Namen an.");
    for (const fine of ["Router", "1", "***", "Router\u3164", "🧑\u200d💻 Terminal", String.fromCodePoint(0x1faff)]) {
      expect(validateAppForm({ ...ok, name: fine }).name, fine).toBeUndefined();
    }
  });

  it("verlangt in einer angegebenen Gruppe ein sichtbares Zeichen, eine leere Gruppe ist in Ordnung", () => {
    expect(validateAppForm({ ...ok, group: "   " }).group).toBeUndefined();
    for (const code of [0x3164, 0xffa0, 0x200d, 0xfe0f, 0x2800]) {
      expect(validateAppForm({ ...ok, group: String.fromCodePoint(code) }).group, `U+${code.toString(16)}`).toBe("Die Gruppe braucht mindestens ein sichtbares Zeichen.");
    }
    expect(validateAppForm({ ...ok, group: "Netzwerk" }).group).toBeUndefined();
  });

  it("nimmt Symbole aus der Liste und Emoji an, aber keine Bild-Adresse", () => {
    expect(validateAppForm({ ...ok, icon: "router" })).toEqual({});
    expect(validateAppForm({ ...ok, icon: "🏠" })).toEqual({});
    expect(validateAppForm({ ...ok, icon: "https://tracker.example/pixel.png" }).icon).toContain("Symbol");
  });
});

describe("bodyFromForm", () => {
  it("trimmt, ergänzt die Adresse, macht Leeres zu null und Farben klein", () => {
    expect(bodyFromForm({ name: "  Pi-hole ", url: " 192.168.2.72/admin ", icon: "", color: "#3B82F6", group: "  ", open_in_new_tab: false, host_id: "" })).toEqual({
      name: "Pi-hole", url: "http://192.168.2.72/admin", icon: null, color: "#3b82f6", group: null, open_in_new_tab: false, host_id: null,
    });
  });
});

describe("Symbol-Auswahl", () => {
  it("hat zu jedem Namen ein Bild und kennt nur diese Namen", () => {
    expect(APP_ICON_NAMES.length).toBeGreaterThan(30);
    expect(new Set(APP_ICON_NAMES).size).toBe(APP_ICON_NAMES.length);
    for (const name of APP_ICON_NAMES) {
      expect(isAppIconName(name)).toBe(true);
      expect(appIconComponent(name)).not.toBeNull();
    }
    expect(isAppIconName("🏠")).toBe(false);
    expect(appIconComponent("🏠")).toBeNull();
    expect(appIconComponent(null)).toBeNull();
  });
});

describe("Gruppen und Filter", () => {
  const apps: AppTileOut[] = [
    tile({ id: "1", name: "FRITZ!Box", group: "Netzwerk", host: "Router" }),
    tile({ id: "2", name: "Pi-hole", group: "Netzwerk" }),
    tile({ id: "3", name: "Synology", group: "Speicher" }),
    tile({ id: "4", name: "Drucker" }),
    tile({ id: "d1", source: "detected", name: "grafana", url: "http://10.0.0.5:3000", image: "grafana/grafana", host: "docker" }),
    tile({ id: "d2", source: "detected", name: "redis", url: null, image: "redis:7", host: "docker" }),
  ];
  const base = (over = {}) => filterApps(apps, { query: "", filter: FILTER_ALL, showAll: false, ...over });

  it("zeigt erkannte Dienste ohne Web-Oberfläche nur auf Wunsch, eigene Apps immer", () => {
    expect(base().map((a) => a.name)).toEqual(["FRITZ!Box", "Pi-hole", "Synology", "Drucker", "grafana"]);
    expect(base({ showAll: true }).map((a) => a.name)).toContain("redis");
  });

  it("sucht in Name, Server, Image, Gruppe und Adresse der eigenen Apps", () => {
    expect(base({ query: "pi" }).map((a) => a.name)).toEqual(["Pi-hole"]);
    expect(base({ query: "router" }).map((a) => a.name)).toEqual(["FRITZ!Box"]);
    expect(base({ query: "speicher" }).map((a) => a.name)).toEqual(["Synology"]);
    expect(base({ query: "grafana/" }).map((a) => a.name)).toEqual(["grafana"]);
    expect(base({ query: "192.168.2" }).map((a) => a.name)).toEqual(["FRITZ!Box", "Pi-hole", "Synology", "Drucker"]);
    expect(base({ query: "gibt-es-nicht" })).toEqual([]);
  });

  it("filtert nach Gruppe, ohne Gruppe und nur erkannten Diensten", () => {
    expect(base({ filter: groupFilter("Netzwerk") }).map((a) => a.name)).toEqual(["FRITZ!Box", "Pi-hole"]);
    expect(base({ filter: FILTER_UNGROUPED }).map((a) => a.name)).toEqual(["Drucker"]);
    expect(base({ filter: FILTER_DETECTED }).map((a) => a.name)).toEqual(["grafana"]);
  });

  it("baut die Knöpfe des Gruppen-Filters in der Reihenfolge der eigenen Apps", () => {
    const chips = groupChips(apps, base());
    expect(chips.map((c) => [c.label, c.count])).toEqual([["Alle", 5], ["Netzwerk", 2], ["Speicher", 1], ["Ohne Gruppe", 1], ["Erkannt", 1]]);
  });

  it("lässt „Erkannt“ bei einer Suche ohne erkannten Treffer stehen (Anzahl 0), wie die Gruppen-Knöpfe", () => {
    const chips = groupChips(apps, base({ query: "fritz" }));
    expect(chips.map((c) => [c.label, c.count])).toEqual([["Alle", 1], ["Netzwerk", 1], ["Speicher", 0], ["Ohne Gruppe", 0], ["Erkannt", 0]]);
  });

  it("zeigt „Erkannt“ nur, wenn es erkannte Dienste gibt, die die Liste zeigt (ohne Web-Oberfläche nur auf Wunsch)", () => {
    const hiddenOnly = [tile({ id: "1", name: "A", group: "G" }), tile({ id: "d2", source: "detected", name: "redis", url: null })];
    const chipsFor = (showAll: boolean) => groupChips(hiddenOnly, filterApps(hiddenOnly, { query: "", filter: FILTER_ALL, showAll }), showAll).map((c) => c.label);
    expect(chipsFor(false)).toEqual(["Alle", "G"]);
    expect(chipsFor(true)).toEqual(["Alle", "G", "Erkannt"]);
  });

  it("zeigt keine Knöpfe, wenn es keine eigene App gibt, und „Ohne Gruppe“ nur neben echten Gruppen", () => {
    const detectedOnly = apps.filter((a) => a.source === "detected");
    expect(groupChips(detectedOnly, filterApps(detectedOnly, { query: "", filter: FILTER_ALL, showAll: true }))).toEqual([]);
    const noGroups = [tile({ id: "1", name: "A" }), tile({ id: "2", name: "B" })];
    expect(groupChips(noGroups, noGroups).map((c) => c.label)).toEqual(["Alle"]);
  });

  it("schlägt vorhandene Gruppen alphabetisch vor", () => {
    expect(existingGroups(apps)).toEqual(["Netzwerk", "Speicher"]);
  });
});
