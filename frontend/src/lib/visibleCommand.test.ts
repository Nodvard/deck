import { describe, expect, it } from "vitest";
import { visibleCommand } from "./visibleCommand";

describe("visibleCommand", () => {
  it("lässt gewöhnliche Befehle unverändert, auch Umlaute, Zeilenumbruch, Tabulator und Leerzeichen", () => {
    const text = "docker restart  web\n\tls -la /srv/größe";
    expect(visibleCommand(text)).toBe(text);
  });

  it("macht die Richtungsumkehr sichtbar", () => {
    expect(visibleCommand("echo \u202Eevil")).toBe("echo ⟦U+202E⟧evil");
    expect(visibleCommand("a\u202A\u202B\u202C\u202Db")).toBe("a⟦U+202A⟧⟦U+202B⟧⟦U+202C⟧⟦U+202D⟧b");
    expect(visibleCommand("a\u2066\u2067\u2068\u2069b")).toBe("a⟦U+2066⟧⟦U+2067⟧⟦U+2068⟧⟦U+2069⟧b");
  });

  it("macht Zeichen ohne Breite sichtbar", () => {
    expect(visibleCommand("rm\u200B -rf")).toBe("rm⟦U+200B⟧ -rf");
    expect(visibleCommand("a\u200C\u200D\u200E\u200Fb")).toBe("a⟦U+200C⟧⟦U+200D⟧⟦U+200E⟧⟦U+200F⟧b");
    expect(visibleCommand("\uFEFFls")).toBe("⟦U+FEFF⟧ls");
  });

  it("macht Wagenrücklauf, Escape und C1-Zeichen sichtbar", () => {
    expect(visibleCommand("harmlos\rrm -rf /")).toBe("harmlos⟦U+000D⟧rm -rf /");
    expect(visibleCommand("\u001b[2Jx")).toBe("⟦U+001B⟧[2Jx");
    expect(visibleCommand("a\u0085b\u009Bc")).toBe("a⟦U+0085⟧b⟦U+009B⟧c");
    expect(visibleCommand("a\u0000b")).toBe("a⟦U+0000⟧b");
  });

  it("markiert Leerzeichen, die die Shell nicht als Trenner liest", () => {
    expect(visibleCommand("rm\u00A0-rf")).toBe("rm⟦U+00A0⟧-rf");
    expect(visibleCommand("a\u3000b\u2003c")).toBe("a⟦U+3000⟧b⟦U+2003⟧c");
    expect(visibleCommand("a\u2028b")).toBe("a⟦U+2028⟧b");
  });

  it("markiert Zeichen oberhalb von U+FFFF mit allen Stellen", () => {
    expect(visibleCommand("x\u{E0041}y")).toBe("x⟦U+E0041⟧y");
  });
});
