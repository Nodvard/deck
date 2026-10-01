import { describe, expect, it } from "vitest";

import { textToKeySteps, type KeyStep } from "./keyboardLayouts";

/** Liest die Tastendruecke als lesbare Folge: "+ShiftLeft KeyA -ShiftLeft" usw. --
 * ein "down"+"up" derselben Taste wird zu einem einzigen Eintrag zusammengefasst. */
function describeSteps(steps: KeyStep[]): string {
  const out: string[] = [];
  for (let i = 0; i < steps.length; i += 1) {
    const s = steps[i];
    const next = steps[i + 1];
    if (s.down && next && !next.down && next.code === s.code) {
      out.push(s.code);
      i += 1;
    } else {
      out.push(`${s.down ? "+" : "-"}${s.code}`);
    }
  }
  return out.join(" ");
}

describe("textToKeySteps", () => {
  it("DE: y und z sind physisch vertauscht, Grossbuchstaben mit Umschalt", () => {
    const { steps, unsupported } = textToKeySteps("zY", "de", { enter: false });
    expect(unsupported).toEqual([]);
    expect(describeSteps(steps)).toBe("KeyY +ShiftLeft KeyZ -ShiftLeft");
  });

  it("US: y und z auf ihren eigenen Tasten", () => {
    expect(describeSteps(textToKeySteps("zy", "us", { enter: false }).steps)).toBe("KeyZ KeyY");
  });

  it("DE: AltGr-Zeichen fuer typische Shell-/Passwort-Sonderzeichen", () => {
    const { steps, unsupported } = textToKeySteps("@{|~\\", "de", { enter: false });
    expect(unsupported).toEqual([]);
    expect(describeSteps(steps)).toBe(
      "+AltRight KeyQ -AltRight +AltRight Digit7 -AltRight +AltRight IntlBackslash -AltRight " +
        "+AltRight BracketRight -AltRight +AltRight Minus -AltRight",
    );
  });

  it("DE: Umlaute, ß und Satzzeichen auf den deutschen Tasten", () => {
    const { steps } = textToKeySteps("äöüß-_/:", "de", { enter: false });
    expect(describeSteps(steps)).toBe(
      "Quote Semicolon BracketLeft Minus Slash +ShiftLeft Slash -ShiftLeft +ShiftLeft Digit7 -ShiftLeft +ShiftLeft Period -ShiftLeft",
    );
  });

  it("DE: tote Taste ^ bekommt ein Leerzeichen hinterher, damit das Zeichen erscheint", () => {
    expect(describeSteps(textToKeySteps("^", "de", { enter: false }).steps)).toBe("Backquote Space");
  });

  it("schickt zu jeder Taste den passenden Keysym mit (fuer Server ohne Scancodes)", () => {
    const { steps } = textToKeySteps("a€", "de", { enter: false });
    const keysyms = steps.filter((s) => s.down && s.code !== "AltRight").map((s) => s.keysym);
    expect(keysyms).toEqual([0x61, 0x01000000 + 0x20ac]);
  });

  it("Zeilenumbrueche druecken Enter nur, wenn ausdruecklich gewuenscht", () => {
    expect(textToKeySteps("ls\n", "us", { enter: false }).unsupported).toEqual(["↵"]);
    const { steps, unsupported } = textToKeySteps("ls\r\n", "us", { enter: true });
    expect(unsupported).toEqual([]);
    expect(describeSteps(steps)).toBe("KeyL KeyS Enter");
  });

  it("meldet nicht tippbare Zeichen statt einen halben Befehl zu schicken", () => {
    expect(textToKeySteps("ok ✓ 😀", "de", { enter: false }).unsupported).toEqual(["✓", "😀"]);
    expect(textToKeySteps("§", "us", { enter: false }).unsupported).toEqual(["§"]);
  });
});
