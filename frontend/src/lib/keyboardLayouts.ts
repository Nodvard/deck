/**
 * Text als Tastendrücke in eine VM-Konsole schicken, denn QEMUs VNC hat keine Zwischenablage. noVNC schickt Scancodes
 * (physische Taste), das Zeichen entscheidet das Layout im Gast (ein „z“ auf der deutschen Tastatur ist physisch das
 * US-„y“): Pro Layout gibt es deshalb eine Tabelle Zeichen -> (Taste, Umschalt/AltGr).
 */

export type LayoutId = "de" | "us";

export const LAYOUT_LABEL: Record<LayoutId, string> = {
  de: "Deutsch (QWERTZ)",
  us: "US (QWERTY)",
};

export interface KeyStep {
  keysym: number;
  code: string;
  down: boolean;
}

type Mod = "" | "S" | "A";
interface KeyDef {
  code: string;
  mod: Mod;
  dead?: boolean;
}

const XK_SHIFT_L = 0xffe1;
const XK_ISO_LEVEL3_SHIFT = 0xfe03; // AltGr
const XK_RETURN = 0xff0d;
const XK_TAB = 0xff09;
const XK_SPACE = 0x20;

function charKeysym(ch: string): number {
  const cp = ch.codePointAt(0)!;
  // X11: Latin-1 direkt, alles andere als Unicode-Keysym (0x01000000 + Codepoint).
  return cp < 0x100 ? cp : 0x01000000 + cp;
}

function lettersAndDigits(map: Map<string, KeyDef>, swapYZ: boolean) {
  for (let i = 0; i < 26; i += 1) {
    const lower = String.fromCharCode(97 + i);
    let code = `Key${lower.toUpperCase()}`;
    if (swapYZ && lower === "y") code = "KeyZ";
    if (swapYZ && lower === "z") code = "KeyY";
    map.set(lower, { code, mod: "" });
    map.set(lower.toUpperCase(), { code, mod: "S" });
  }
  for (let d = 0; d <= 9; d += 1) map.set(String(d), { code: `Digit${d}`, mod: "" });
  map.set(" ", { code: "Space", mod: "" });
}

function build(entries: [string, string, Mod, boolean?][], swapYZ: boolean): Map<string, KeyDef> {
  const map = new Map<string, KeyDef>();
  lettersAndDigits(map, swapYZ);
  for (const [ch, code, mod, dead] of entries) map.set(ch, { code, mod, dead });
  return map;
}

const US = build(
  [
    ["!", "Digit1", "S"], ["@", "Digit2", "S"], ["#", "Digit3", "S"], ["$", "Digit4", "S"], ["%", "Digit5", "S"],
    ["^", "Digit6", "S"], ["&", "Digit7", "S"], ["*", "Digit8", "S"], ["(", "Digit9", "S"], [")", "Digit0", "S"],
    ["-", "Minus", ""], ["_", "Minus", "S"], ["=", "Equal", ""], ["+", "Equal", "S"],
    ["[", "BracketLeft", ""], ["{", "BracketLeft", "S"], ["]", "BracketRight", ""], ["}", "BracketRight", "S"],
    ["\\", "Backslash", ""], ["|", "Backslash", "S"], [";", "Semicolon", ""], [":", "Semicolon", "S"],
    ["'", "Quote", ""], ['"', "Quote", "S"], ["`", "Backquote", ""], ["~", "Backquote", "S"],
    [",", "Comma", ""], ["<", "Comma", "S"], [".", "Period", ""], [">", "Period", "S"],
    ["/", "Slash", ""], ["?", "Slash", "S"],
  ],
  false,
);

const DE = build(
  [
    ["!", "Digit1", "S"], ['"', "Digit2", "S"], ["§", "Digit3", "S"], ["$", "Digit4", "S"], ["%", "Digit5", "S"],
    ["&", "Digit6", "S"], ["/", "Digit7", "S"], ["(", "Digit8", "S"], [")", "Digit9", "S"], ["=", "Digit0", "S"],
    ["²", "Digit2", "A"], ["³", "Digit3", "A"], ["{", "Digit7", "A"], ["[", "Digit8", "A"], ["]", "Digit9", "A"], ["}", "Digit0", "A"],
    ["ß", "Minus", ""], ["?", "Minus", "S"], ["\\", "Minus", "A"],
    ["´", "Equal", "", true], ["`", "Equal", "S", true],
    ["^", "Backquote", "", true], ["°", "Backquote", "S"],
    ["ü", "BracketLeft", ""], ["Ü", "BracketLeft", "S"],
    ["+", "BracketRight", ""], ["*", "BracketRight", "S"], ["~", "BracketRight", "A"],
    ["ö", "Semicolon", ""], ["Ö", "Semicolon", "S"], ["ä", "Quote", ""], ["Ä", "Quote", "S"],
    ["#", "Backslash", ""], ["'", "Backslash", "S"],
    [",", "Comma", ""], [";", "Comma", "S"], [".", "Period", ""], [":", "Period", "S"],
    ["-", "Slash", ""], ["_", "Slash", "S"],
    ["<", "IntlBackslash", ""], [">", "IntlBackslash", "S"], ["|", "IntlBackslash", "A"],
    ["@", "KeyQ", "A"], ["€", "KeyE", "A"], ["µ", "KeyM", "A"],
  ],
  true,
);

const LAYOUTS: Record<LayoutId, Map<string, KeyDef>> = { de: DE, us: US };

function press(steps: KeyStep[], keysym: number, code: string) {
  steps.push({ keysym, code, down: true }, { keysym, code, down: false });
}

export interface TextToKeysResult {
  steps: KeyStep[];
  unsupported: string[];
}

/**
 * Wandelt Text in eine Folge von Tastendrücken. `enter`: ob ein Zeilenumbruch die Eingabetaste drückt, bewusst
 * Entscheidung des Aufrufers (ein eingefügtes Mehrzeilen-Skript läuft dann Zeile für Zeile). Zeichen, die das
 * Layout nicht kennt, landen in `unsupported`; der Aufrufer schickt dann gar nichts statt eines halben Befehls.
 */
export function textToKeySteps(text: string, layout: LayoutId, { enter }: { enter: boolean }): TextToKeysResult {
  const map = LAYOUTS[layout];
  const steps: KeyStep[] = [];
  const unsupported = new Set<string>();
  for (const ch of text.replace(/\r\n?/g, "\n")) {
    if (ch === "\n") {
      if (enter) press(steps, XK_RETURN, "Enter");
      else unsupported.add("↵");
      continue;
    }
    if (ch === "\t") {
      press(steps, XK_TAB, "Tab");
      continue;
    }
    const def = map.get(ch);
    if (!def) {
      unsupported.add(ch);
      continue;
    }
    if (def.mod === "S") steps.push({ keysym: XK_SHIFT_L, code: "ShiftLeft", down: true });
    if (def.mod === "A") steps.push({ keysym: XK_ISO_LEVEL3_SHIFT, code: "AltRight", down: true });
    // Zusätzlich geht immer der passende Keysym mit: Server ohne Scancode-Unterstützung (z. B. Proxmox' vncterm für
    // LXC) nehmen den und brauchen kein Layout.
    press(steps, charKeysym(ch), def.code);
    if (def.mod === "A") steps.push({ keysym: XK_ISO_LEVEL3_SHIFT, code: "AltRight", down: false });
    if (def.mod === "S") steps.push({ keysym: XK_SHIFT_L, code: "ShiftLeft", down: false });
    // Tote Tasten (^ ` ´ im deutschen Layout) mit einem Leerzeichen danach, damit das Zeichen selbst erscheint statt
    // auf den nächsten Buchstaben zu warten.
    if (def.dead) press(steps, XK_SPACE, "Space");
  }
  return { steps, unsupported: [...unsupported] };
}
